# Gamezone — Website Reservasi (Flask)

```bash
pip install -r requirements.txt
python app.py
```
Website satu halaman: http://127.0.0.1:5000 (Beranda, Unit & Harga, Reservasi, Kontak) — panel admin di /admin (tanpa password; link tidak ditampilkan di situs publik).
Untuk mengaktifkan login: set `REQUIRE_ADMIN_LOGIN = True` di app.py dan env `ADMIN_PASSWORD`.

## File yang perlu kamu isi
- `static/img/qris.png`   → gambar QRIS pembayaran
- `static/img/logo.png`   → logo (sudah terpasang)

## Pengaturan (bagian KONFIGURASI di app.py)
Alamat, WhatsApp, Instagram, TikTok, jam buka, `PROOF_REQUIRED` (bukti bayar wajib/opsional).
Env: `ADMIN_PASSWORD`, `ADMIN_WA`, `SECRET_KEY`.

## Data
Database: `instance/reservasi.db`. Bukti bayar: `instance/uploads/` (hanya bisa dibuka admin).
Ruangan, unit, harga, dan daftar game bisa diubah di Admin > Kelola Unit.
