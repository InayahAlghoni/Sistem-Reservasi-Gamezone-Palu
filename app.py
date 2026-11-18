import hmac
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)

app = Flask(__name__)

# Folder "instance" dipakai untuk menyimpan database, kunci rahasia, dan bukti bayar
os.makedirs(app.instance_path, exist_ok=True)
UPLOAD_DIR = os.path.join(app.instance_path, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
DB_PATH = os.path.join(app.instance_path, "reservasi.db")


# ===================== KONFIGURASI ============================================
BRAND = "Gamezone"
TAGLINE = "Rental PS3, PS4, PS5, Nintendo Switch, dan karaoke di Kota Palu."
ADDRESS = "Jl. Batu Bata Indah, Kota Palu."
OPEN_TEXT = "Setiap hari, 09.00 – 01.00"
ADMIN_WA = os.environ.get("ADMIN_WA", "6282193706449")  # format 62xxxx, tanpa tanda +
INSTAGRAM_URL = "https://www.instagram.com/gamezone.plw?cplk=MzZieGs5b2wycmk="
TIKTOK_URL = "https://www.tiktok.com/@gamezone.plw"

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")  # jangan lupa diganti!
REQUIRE_ADMIN_LOGIN = False  # False = panel admin terbuka tanpa password
PROOF_REQUIRED = True        # True = pelanggan wajib upload bukti pembayaran

TZ = timezone(timedelta(hours=8))  # WITA (zona waktu Palu)
OPEN_HOUR, CLOSE_HOUR = 9, 25      # buka jam 09:00, tutup jam 01:00 (25 = jam 1 dini hari)
MAX_DAYS_AHEAD = 7                 # pelanggan bisa booking maksimal 7 hari ke depan
DURATIONS = [1, 2, 3, 4]           # pilihan durasi (jam)
MAX_PROOF_MB = 5                   # batas ukuran file bukti bayar
# ==============================================================================


# Status reservasi
PENDING, VERIFY, CONFIRMED, DONE, CANCELLED = "pending", "verifikasi", "dikonfirmasi", "selesai", "dibatalkan"

# Teks yang tampil di layar untuk tiap status
STATUS_LABEL = {
    PENDING: "Pending",
    VERIFY: "Menunggu Verifikasi",
    CONFIRMED: "Dikonfirmasi",
    DONE: "Selesai",
    CANCELLED: "Dibatalkan",
}

# Status yang dianggap "memakai" jam (yang dibatalkan otomatis kosong lagi)
OCCUPYING = (PENDING, VERIFY, CONFIRMED, DONE)

# Aturan perpindahan status: dari status X boleh lanjut ke mana saja
TRANSITIONS = {
    PENDING: [CONFIRMED, CANCELLED],
    VERIFY: [CONFIRMED, CANCELLED],
    CONFIRMED: [DONE, CANCELLED],
    DONE: [],
    CANCELLED: [],
}


def _secret_key():
    """Ambil SECRET_KEY dari environment. Kalau tidak ada, buat sendiri lalu simpan di file."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]

    path = os.path.join(app.instance_path, "secret.key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path) as f:
        return f.read().strip()


app.config.update(
    SECRET_KEY=_secret_key(),
    MAX_CONTENT_LENGTH=(MAX_PROOF_MB + 1) * 1024 * 1024,  # batas total upload
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)


# ================================ DATABASE ====================================
# rooms    : daftar ruangan (VIP, Reguler, Smoking)
# units    : konsol di dalam ruangan, lengkap dengan harga per jam
# bookings : semua reservasi, baik dari web maupun blokir manual oleh admin
SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    smoking INTEGER NOT NULL DEFAULT 0,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    label TEXT NOT NULL,
    console TEXT NOT NULL,
    extras TEXT NOT NULL DEFAULT '',
    price INTEGER NOT NULL,
    games TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    unit_id INTEGER NOT NULL REFERENCES units(id),
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    date TEXT NOT NULL,
    start_hour INTEGER NOT NULL,
    duration INTEGER NOT NULL,
    total INTEGER NOT NULL,
    status TEXT NOT NULL,
    proof TEXT,
    source TEXT NOT NULL DEFAULT 'web',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_b_unit_date ON bookings(unit_id, date);
"""

# Data awal (contoh saja). Semuanya bisa diubah lewat Admin > Kelola Unit
GAMES = {
    "PS3": "FIFA 14, PES 2014, GTA V, Tekken Tag Tournament 2, WWE 2K14, Need for Speed Most Wanted",
    "PS4": "EA Sports FC, GTA V, Tekken 7, Mortal Kombat 11, Naruto Storm 4, God of War",
    "PS5": "EA Sports FC, Tekken 8, Spider-Man 2, God of War Ragnarök, It Takes Two, Gran Turismo 7",
    "SWITCH": "Mario Kart 8 Deluxe, Super Smash Bros. Ultimate, Mario Party Superstars, Overcooked 2",
}

VIP_DESC = ("Paket lengkap dalam satu ruangan: PS5, Nintendo Switch, dan karaoke. "
            "Sofa bed, sofa lesehan, Full AC, No smoking.")


def init_db():
    """Buat tabel kalau belum ada, lalu isi data awal kalau database masih kosong."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)

    if not conn.execute("SELECT 1 FROM rooms").fetchone():
        rooms = [
            ("vip", "VIP Room", VIP_DESC, 0, 1),
            ("reguler", "Reguler Room", "Ruangan bersama dengan sofa dan Full AC. No smoking.", 0, 2),
            ("smoking", "Smoking Room", "Ruangan bersama dengan sofa dan Full AC. Boleh merokok.", 1, 3),
        ]
        conn.executemany(
            "INSERT INTO rooms (slug,name,description,smoking,sort) VALUES (?,?,?,?,?)", rooms)

        # peta slug -> id ruangan, supaya unit bisa dihubungkan ke ruangannya
        room_id = {slug: rid for rid, slug in conn.execute("SELECT id, slug FROM rooms")}

        # Satu unit VIP
        units = [(room_id["vip"], "VIP Room", "PS5 + Nintendo Switch + Karaoke", "Netflix Premium",
                  35000, GAMES["PS5"] + ", " + GAMES["SWITCH"], 1)]

        # Ruang Reguler: 4 unit (unit ke-4 PS3, sisanya PS4)
        for i in range(1, 5):
            is_ps3 = (i == 4)
            console = "PS3" if is_ps3 else "PS4"
            price = 8000 if is_ps3 else 10000
            units.append((room_id["reguler"], f"Reguler {i}", console, "", price, GAMES[console], i + 1))

        # Ruang Smoking: 5 unit (unit ke-4 dan ke-5 PS3, sisanya PS4)
        for i in range(1, 6):
            is_ps3 = (i >= 4)
            console = "PS3" if is_ps3 else "PS4"
            price = 8000 if is_ps3 else 10000
            units.append((room_id["smoking"], f"Smoking {i}", console, "", price, GAMES[console], i))

        conn.executemany(
            "INSERT INTO units (room_id,label,console,extras,price,games,sort) VALUES (?,?,?,?,?,?,?)", units)

    # Perbaikan untuk data lama: karaoke dipindah ke nama konsol VIP
    conn.execute("UPDATE units SET console='PS5 + Nintendo Switch + Karaoke', extras='Netflix Premium' "
                 "WHERE console='PS5 + Nintendo Switch' AND extras='Netflix Premium, Karaoke'")
    old_desc = "Paket lengkap dalam satu ruangan. Sofa bed, sofa lesehan, Full AC, No smoking."
    conn.execute("UPDATE rooms SET description=? WHERE slug='vip' AND description=?", (VIP_DESC, old_desc))

    conn.commit()
    conn.close()


def get_db():
    """Koneksi database untuk satu request (dipakai ulang selama request itu)."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, isolation_level=None)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    """Tutup koneksi otomatis setiap request selesai."""
    db = g.pop("db", None)
    if db:
        db.close()


# ================================ HELPER ======================================
DAYS = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
MONTHS = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus",
          "September", "Oktober", "November", "Desember"]


def now():
    """Waktu sekarang dalam WITA."""
    return datetime.now(TZ)


def parse_date(value, days_ahead=MAX_DAYS_AHEAD):
    """Ubah teks 'YYYY-MM-DD' jadi tanggal. Hasilnya None kalau salah format atau di luar rentang."""
    try:
        d = datetime.strptime(value or "", "%Y-%m-%d").date()
    except ValueError:
        return None
    today = now().date()
    if today <= d <= today + timedelta(days=days_ahead):
        return d
    return None


def normalize_phone(raw):
    """Rapikan nomor HP ke format 8xxxxxxxxx (tanpa 0 / 62). Return None kalau tidak valid."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("62"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = digits[1:]
    if re.fullmatch(r"8\d{8,12}", digits):
        return digits
    return None


def static_exists(rel):
    """Cek apakah file di folder static ada (misalnya logo atau QRIS)."""
    return os.path.exists(os.path.join(app.static_folder, rel))


# Filter untuk template HTML, contoh pemakaian: {{ harga|rupiah }}
@app.template_filter("rupiah")
def f_rupiah(n):
    return "Rp" + f"{int(n):,}".replace(",", ".")


@app.template_filter("tanggal")
def f_tanggal(s):
    d = datetime.strptime(str(s), "%Y-%m-%d")
    return f"{DAYS[d.weekday()]}, {d.day} {MONTHS[d.month - 1]} {d.year}"


@app.template_filter("tanggal_pendek")
def f_tanggal_pendek(s):
    d = datetime.strptime(str(s), "%Y-%m-%d")
    return f"{DAYS[d.weekday()][:3]}, {d.day} {MONTHS[d.month - 1][:3]}"


@app.template_filter("jam")
def f_jam(h):
    # jam 25 jadi 01:00, dst
    return f"{int(h) % 24:02d}:00"


@app.template_filter("csv")
def f_csv(s):
    """Pecah teks 'a, b, c' jadi daftar ['a', 'b', 'c']."""
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def csrf_token():
    """Token pengaman form, disimpan di session."""
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@app.context_processor
def inject():
    """Variabel yang otomatis tersedia di semua template."""
    return dict(
        brand=BRAND, tagline=TAGLINE, address=ADDRESS, open_text=OPEN_TEXT,
        admin_wa=ADMIN_WA, instagram=INSTAGRAM_URL, tiktok=TIKTOK_URL,
        csrf_token=csrf_token, STATUS_LABEL=STATUS_LABEL, static_exists=static_exists,
        is_admin=is_admin(), require_login=REQUIRE_ADMIN_LOGIN,
    )


@app.before_request
def csrf_protect():
    """Setiap request POST harus membawa token CSRF yang cocok."""
    if request.method == "POST":
        sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token", "")
        if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
            if request.path.startswith("/api/"):
                return jsonify(error="Sesi kedaluwarsa. Muat ulang halaman."), 400
            abort(400)


@app.after_request
def security_headers(resp):
    """Tambahkan header keamanan dasar ke setiap respon."""
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


@app.errorhandler(413)
def too_large(_e):
    """Dipanggil kalau file yang diunggah kebesaran."""
    msg = f"Ukuran file maksimal {MAX_PROOF_MB} MB."
    if request.path.startswith("/api/"):
        return jsonify(error=msg), 413
    flash(msg, "error")
    return redirect(request.referrer or url_for("index"))


def room_units(db, only_active=True):
    """Ambil semua ruangan beserta unit-unitnya. Hasilnya: [{'room': {...}, 'units': [...]}, ...]"""
    hasil = []
    for r in db.execute("SELECT * FROM rooms ORDER BY sort, id").fetchall():
        query = "SELECT * FROM units WHERE room_id=?"
        if only_active:
            query += " AND active=1"
        query += " ORDER BY sort, id"
        units = [dict(u) for u in db.execute(query, (r["id"],))]
        hasil.append({"room": dict(r), "units": units})
    return hasil


# ============================== LOGIKA BOOKING ================================
def busy_hours(db, unit_id, date_str):
    """Kumpulan jam yang sudah terisi untuk satu unit pada satu tanggal."""
    hours = set()
    marks = ",".join("?" * len(OCCUPYING))
    rows = db.execute(
        f"SELECT start_hour, duration FROM bookings WHERE unit_id=? AND date=? AND status IN ({marks})",
        (unit_id, date_str, *OCCUPYING))
    for r in rows:
        hours.update(range(r["start_hour"], r["start_hour"] + r["duration"]))
    return hours


def slot_state(busy, d, start, duration, allow_past=False):
    """
    Tentukan kondisi sebuah slot:
      closed -> di luar jam buka
      past   -> jamnya sudah lewat (hari ini)
      booked -> bentrok dengan reservasi lain
      free   -> masih kosong
    """
    if start < OPEN_HOUR or start + duration > CLOSE_HOUR:
        return "closed"
    if not allow_past and d == now().date() and start <= now().hour:
        return "past"
    if any(h in busy for h in range(start, start + duration)):
        return "booked"
    return "free"


class BookingError(Exception):
    """Error khusus booking, membawa pesan dan kode HTTP."""

    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def create_booking(db, *, unit_id, name, phone, d, start, duration, status, source="web",
                   proof=None, note="", max_duration=4, allow_past=False):
    """Simpan reservasi baru. Return (kode_booking, total_harga)."""
    unit = db.execute("SELECT * FROM units WHERE id=? AND active=1", (unit_id,)).fetchone()
    if not unit:
        raise BookingError("Unit tidak ditemukan.")
    if not 1 <= duration <= max_duration:
        raise BookingError("Durasi tidak valid.")

    # Blokir manual dari admin tidak dihitung biaya
    total = 0 if source == "manual" else unit["price"] * duration
    code = "GZ-" + secrets.token_hex(3).upper()

    # BEGIN IMMEDIATE mengunci database, jadi dua orang tidak bisa booking slot yang sama
    db.execute("BEGIN IMMEDIATE")
    try:
        busy = busy_hours(db, unit_id, d.isoformat())
        if slot_state(busy, d, start, duration, allow_past) != "free":
            raise BookingError("Slot sudah terisi atau tidak tersedia. Silakan pilih jam lain.", 409)

        db.execute(
            "INSERT INTO bookings (code,unit_id,name,phone,date,start_hour,duration,total,status,proof,source,note,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, unit_id, name, phone, d.isoformat(), start, duration, total,
             status, proof, source, note, now().isoformat()))
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    return code, total


def save_proof(f):
    """
    Simpan bukti bayar (JPG/PNG/WEBP/PDF).
    Jenis file dicek dari isi file-nya, bukan dari nama, supaya tidak bisa dipalsukan.
    Return nama file baru, atau None kalau tidak ada file.
    """
    if not f or not f.filename:
        return None

    head = f.stream.read(12)
    f.stream.seek(0)

    if head.startswith(b"\x89PNG"):
        ext = "png"
    elif head[:3] == b"\xff\xd8\xff":
        ext = "jpg"
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        ext = "webp"
    elif head[:4] == b"%PDF":
        ext = "pdf"
    else:
        raise ValueError("Format bukti harus JPG, PNG, WEBP, atau PDF.")

    name = secrets.token_hex(12) + "." + ext
    f.save(os.path.join(UPLOAD_DIR, name))
    return name


def booking_rows(db, where="", args=()):
    """Ambil data reservasi lengkap dengan nama unit, konsol, dan ruangannya."""
    return db.execute(
        "SELECT b.*, u.label AS unit_label, u.console, r.name AS room_name FROM bookings b "
        "JOIN units u ON u.id=b.unit_id JOIN rooms r ON r.id=u.room_id " + where, args).fetchall()


# ============================ HALAMAN PUBLIK ==================================
@app.get("/")
def index():
    """Halaman utama (satu halaman): daftar ruangan, unit, dan form reservasi."""
    today = now().date()
    groups = room_units(get_db())

    # Data yang dikirim ke JavaScript di halaman
    cfg = {
        "rooms": [{"id": grup["room"]["id"], "name": grup["room"]["name"]}
                  for grup in groups if grup["units"]],
        "units": [{"id": u["id"], "room": grup["room"]["id"], "label": u["label"],
                   "console": u["console"], "extras": u["extras"], "price": u["price"]}
                  for grup in groups for u in grup["units"]],
        "durations": DURATIONS,
        "today": today.isoformat(),
        "maxDate": (today + timedelta(days=MAX_DAYS_AHEAD)).isoformat(),
        "proofRequired": PROOF_REQUIRED,
        "maxProofMb": MAX_PROOF_MB,
        "csrf": csrf_token(),
        "adminWa": ADMIN_WA,
        "brand": BRAND,
    }
    return render_template("index.html", groups=groups, cfg=cfg,
                           logo=static_exists("img/logo.png"),
                           has_qris=static_exists("img/qris.png"),
                           proof_required=PROOF_REQUIRED, max_mb=MAX_PROOF_MB)


@app.get("/api/slots")
def api_slots():
    """Daftar jam (09:00 s/d 00:00) beserta status tersedia atau tidak, untuk unit + tanggal + durasi tertentu."""
    d = parse_date(request.args.get("date"))
    unit_id = request.args.get("unit", type=int)
    duration = request.args.get("duration", type=int)

    if not d or duration not in DURATIONS or not unit_id:
        return jsonify(error="Parameter tidak valid."), 400

    busy = busy_hours(get_db(), unit_id, d.isoformat())
    slots = []
    for h in range(OPEN_HOUR, CLOSE_HOUR):
        slots.append({
            "hour": h,
            "label": f"{h % 24:02d}:00",
            "available": slot_state(busy, d, h, duration) == "free",
        })
    return jsonify(slots=slots)


@app.post("/api/reservasi")
def api_reservasi():
    """Terima form reservasi dari pelanggan, validasi, lalu simpan."""
    form, db = request.form, get_db()

    name = (form.get("name") or "").strip()
    phone = normalize_phone(form.get("phone"))
    d = parse_date(form.get("date"))

    try:
        unit_id = int(form.get("unit"))
        duration = int(form.get("duration"))
        start = int(form.get("start"))
    except (TypeError, ValueError):
        return jsonify(error="Data reservasi belum lengkap."), 400

    if not 2 <= len(name) <= 60:
        return jsonify(error="Nama minimal 2 karakter."), 400
    if not phone:
        return jsonify(error="Nomor WhatsApp tidak valid."), 400
    if not d or duration not in DURATIONS:
        return jsonify(error="Tanggal atau durasi tidak valid."), 400

    upload = request.files.get("proof")
    if PROOF_REQUIRED and not (upload and upload.filename):
        return jsonify(error="Bukti pembayaran wajib diunggah."), 400

    try:
        proof = save_proof(upload)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    try:
        # Kalau ada bukti bayar, status langsung "menunggu verifikasi"
        code, total = create_booking(
            db, unit_id=unit_id, name=name, phone=phone, d=d, start=start,
            duration=duration, status=VERIFY if proof else PENDING, proof=proof)
    except BookingError as e:
        # Booking gagal, jadi file bukti yang sudah tersimpan dihapus lagi
        if proof:
            os.remove(os.path.join(UPLOAD_DIR, proof))
        return jsonify(error=str(e)), e.code

    u = db.execute(
        "SELECT u.label, u.console, r.name AS room FROM units u JOIN rooms r ON r.id=u.room_id "
        "WHERE u.id=?", (unit_id,)).fetchone()

    return jsonify(booking={
        "code": code,
        "name": name,
        "phone": "+62" + phone,
        "room": u["room"],
        "unit": f'{u["label"]} ({u["console"]})',
        "date": d.isoformat(),
        "start": f"{start % 24:02d}:00",
        "end": f"{(start + duration) % 24:02d}:00",
        "duration": duration,
        "total": total,
    }), 201


# ================================== ADMIN =====================================
LOGIN_ATTEMPTS = {}  # catatan percobaan login per IP (untuk batasi tebak password)


def is_admin():
    """True kalau login tidak diwajibkan, atau sesi admin masih berlaku (8 jam)."""
    if not REQUIRE_ADMIN_LOGIN:
        return True
    return time.time() - session.get("admin_at", 0) < 8 * 3600


def admin_required(fn):
    """Decorator: halaman yang dibungkus ini hanya bisa dibuka admin."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_admin():
            return redirect(url_for("admin_login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if not REQUIRE_ADMIN_LOGIN:
        return redirect(url_for("admin"))

    if request.method == "POST":
        ip = request.remote_addr or "-"

        # hanya hitung percobaan dalam 5 menit terakhir
        recent = [t for t in LOGIN_ATTEMPTS.get(ip, []) if time.time() - t < 300]
        LOGIN_ATTEMPTS[ip] = recent

        if len(recent) >= 5:
            flash("Terlalu banyak percobaan. Coba lagi dalam beberapa menit.", "error")
        elif hmac.compare_digest((request.form.get("password") or "").encode(), ADMIN_PASSWORD.encode()):
            session["admin_at"] = time.time()
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/admin") else url_for("admin"))
        else:
            recent.append(time.time())
            flash("Password salah.", "error")

    return render_template("admin_login.html")


@app.post("/admin/logout")
def admin_logout():
    session.pop("admin_at", None)
    return redirect(url_for("index"))


@app.get("/admin")
@admin_required
def admin():
    """Dashboard: antrean verifikasi di atas, riwayat reservasi di bawah."""
    db = get_db()

    # Reservasi dari web yang masih perlu dicek admin
    verify = booking_rows(
        db, "WHERE b.source='web' AND b.status IN (?,?) ORDER BY b.status DESC, b.id", (VERIFY, PENDING))

    # Riwayat 100 reservasi terakhir, bisa difilter berdasarkan status
    flt = request.args.get("status", "")
    where, args = "WHERE b.source='web'", []
    if flt in STATUS_LABEL:
        where += " AND b.status=?"
        args.append(flt)
    rows = booking_rows(db, where + " ORDER BY b.id DESC LIMIT 100", args)

    today = now().date().isoformat()
    stats = {
        "verify": sum(1 for r in verify if r["status"] == VERIFY),
        "pending": sum(1 for r in verify if r["status"] == PENDING),
        "today": db.execute("SELECT COUNT(*) FROM bookings WHERE date=? AND status IN (?,?,?)",
                            (today, VERIFY, CONFIRMED, DONE)).fetchone()[0],
        "total": db.execute("SELECT COUNT(*) FROM bookings WHERE source='web'").fetchone()[0],
    }

    # Peringatan di dashboard kalau password admin masih bawaan
    default_pw = REQUIRE_ADMIN_LOGIN and ADMIN_PASSWORD == "admin123"

    return render_template("admin_dashboard.html", verify=verify, rows=rows, stats=stats,
                           flt=flt, default_pw=default_pw, transitions=TRANSITIONS)


@app.post("/admin/reservasi/<int:bid>/status")
@admin_required
def admin_set_status(bid):
    """Ubah status reservasi (misal dari Menunggu Verifikasi ke Dikonfirmasi)."""
    db, new = get_db(), request.form.get("status")
    row = db.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()

    if row and new in TRANSITIONS.get(row["status"], []):
        db.execute("UPDATE bookings SET status=? WHERE id=?", (new, bid))
        flash(f"{row['code']} → {STATUS_LABEL[new]}", "ok")
    else:
        flash("Perubahan status tidak valid.", "error")
    return redirect(request.referrer or url_for("admin"))


@app.get("/admin/bukti/<path:filename>")
@admin_required
def admin_proof(filename):
    """Tampilkan file bukti pembayaran (hanya untuk admin)."""
    return send_from_directory(UPLOAD_DIR, filename)


@app.get("/admin/unit")
@admin_required
def admin_unit():
    """Halaman kelola ruangan dan unit."""
    return render_template("admin_unit.html", groups=room_units(get_db(), only_active=False))


@app.post("/admin/ruangan/<int:rid>/simpan")
@admin_required
def admin_room_save(rid):
    """Simpan perubahan nama dan deskripsi ruangan."""
    form = request.form
    name = (form.get("name") or "").strip()

    if not name:
        flash("Nama ruangan tidak boleh kosong.", "error")
    else:
        desc = (form.get("description") or "").strip()[:300]
        get_db().execute("UPDATE rooms SET name=?, description=? WHERE id=?", (name[:60], desc, rid))
        flash("Ruangan disimpan.", "ok")
    return redirect(url_for("admin_unit"))


@app.post("/admin/unit/simpan")
@admin_required
def admin_unit_save():
    """Tambah unit baru, atau update unit lama (kalau form membawa id)."""
    form, db = request.form, get_db()

    try:
        uid = int(form["id"]) if form.get("id") else None
        room_id = int(form["room_id"])
        price = int(form["price"])
        sort = int(form.get("sort") or 0)
        if price < 0:
            raise ValueError
    except (KeyError, ValueError):
        flash("Data unit tidak valid (cek ruangan dan harga).", "error")
        return redirect(url_for("admin_unit"))

    label = (form.get("label") or "").strip()[:40]
    console = (form.get("console") or "").strip()[:60]
    if not label or not console:
        flash("Nama unit dan konsol wajib diisi.", "error")
        return redirect(url_for("admin_unit"))

    vals = (room_id, label, console,
            (form.get("extras") or "").strip()[:120],
            price,
            (form.get("games") or "").strip()[:600],
            1 if form.get("active") else 0,
            sort)

    if uid:
        db.execute("UPDATE units SET room_id=?,label=?,console=?,extras=?,price=?,games=?,active=?,sort=? WHERE id=?",
                   (*vals, uid))
    else:
        db.execute("INSERT INTO units (room_id,label,console,extras,price,games,active,sort) VALUES (?,?,?,?,?,?,?,?)",
                   vals)
    flash("Unit disimpan.", "ok")
    return redirect(url_for("admin_unit"))


@app.post("/admin/unit/<int:uid>/hapus")
@admin_required
def admin_unit_delete(uid):
    """Hapus unit. Kalau sudah punya riwayat reservasi, hanya boleh dinonaktifkan."""
    db = get_db()
    if db.execute("SELECT 1 FROM bookings WHERE unit_id=?", (uid,)).fetchone():
        flash("Unit punya riwayat reservasi, jadi tidak bisa dihapus. Nonaktifkan saja.", "error")
    else:
        db.execute("DELETE FROM units WHERE id=?", (uid,))
        flash("Unit dihapus.", "ok")
    return redirect(url_for("admin_unit"))


@app.route("/admin/blokir", methods=["GET", "POST"])
@admin_required
def admin_block():
    """Admin menandai slot sebagai terisi (misal pelanggan datang langsung / walk-in)."""
    db = get_db()

    if request.method == "POST":
        form = request.form
        d = parse_date(form.get("date"), days_ahead=60)
        try:
            unit_id = int(form["unit"])
            start = int(form["start"])
            duration = int(form["duration"])
        except (KeyError, ValueError):
            unit_id = start = duration = 0
        note = (form.get("note") or "").strip()[:60]

        if not d or not unit_id:
            flash("Pilih unit dan tanggal yang valid.", "error")
        else:
            try:
                create_booking(db, unit_id=unit_id, name=note or "Blokir manual", phone="-", d=d,
                               start=start, duration=duration, status=CONFIRMED, source="manual",
                               note=note, max_duration=12, allow_past=True)
                flash("Slot ditandai terbooking.", "ok")
            except BookingError as e:
                flash(str(e), "error")
        return redirect(url_for("admin_block"))

    # Tampilan GET: daftar blokir yang masih berlaku + form tambah
    today = now().date()
    blocks = booking_rows(db, "WHERE b.source='manual' AND b.date>=? ORDER BY b.date, b.start_hour",
                          (today.isoformat(),))
    return render_template("admin_blokir.html", groups=room_units(db), blocks=blocks,
                           today=today.isoformat(),
                           max_date=(today + timedelta(days=60)).isoformat(),
                           hours=list(range(OPEN_HOUR, CLOSE_HOUR)))


@app.post("/admin/blokir/<int:bid>/hapus")
@admin_required
def admin_block_delete(bid):
    """Hapus blokir manual supaya slot bisa dipesan lagi."""
    get_db().execute("DELETE FROM bookings WHERE id=? AND source='manual'", (bid,))
    flash("Blokir dihapus, slot kembali tersedia.", "ok")
    return redirect(url_for("admin_block"))


# Siapkan database setiap aplikasi dijalankan
init_db()

if __name__ == "__main__":
    app.run(debug=True)