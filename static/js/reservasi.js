(() => {
  const C = window.GZ;
  const $ = (s) => document.querySelector(s);
  const rupiah = (n) => 'Rp' + Math.round(n).toLocaleString('id-ID');
  const fmtDate = (iso) => new Intl.DateTimeFormat('id-ID', {weekday:'long', day:'numeric', month:'long', year:'numeric'}).format(new Date(iso + 'T00:00:00'));
  const pad = (h) => String(h % 24).padStart(2, '0') + ':00';
  const el = {room:$('#room'), unit:$('#unit-sel'), info:$('#unitInfo'), date:$('#date'), dateView:$('#dateView'),
    durations:$('#durations'), startBtn:$('#startBtn'), startText:$('#startText'), slots:$('#slots'),
    summary:$('#summary'), payTotal:$('#payTotal'), proof:$('#proof'), proofName:$('#proofName'), toast:$('#toast')};
  const st = {unit:null, date:C.today, duration:null, start:null, slots:[]};
  const unitById = (id) => C.units.find(u => u.id === +id);
  const roomName = (id) => (C.rooms.find(r => r.id === +id) || {}).name || '';

  function fillUnits(roomId, pick) {
    el.unit.replaceChildren(...C.units.filter(u => u.room === +roomId).map(u => {
      const o = document.createElement('option');
      o.value = u.id; o.textContent = `${u.label} — ${u.console} · ${rupiah(u.price)}/jam`; return o;
    }));
    if (pick && C.units.some(u => u.id === pick && u.room === +roomId)) el.unit.value = pick;
    st.unit = +el.unit.value || null;
    const u = unitById(st.unit);
    el.info.textContent = u ? [u.console, u.extras].filter(Boolean).join(' · ') : '';
  }

  /* ---- durasi ---- */
  C.durations.forEach(d => {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'dur display'; b.dataset.d = d; b.textContent = d + ' jam'; b.setAttribute('aria-pressed', 'false');
    el.durations.append(b);
  });
  function setDuration(d) {
    st.duration = d;
    el.durations.querySelectorAll('.dur').forEach(x => x.setAttribute('aria-pressed', +x.dataset.d === d));
  }
  el.durations.addEventListener('click', (e) => {
    const b = e.target.closest('.dur'); if (!b) return;
    setDuration(+b.dataset.d); refreshSlots(true); update();
  });

  /* ---- tanggal ---- */
  el.date.min = C.today; el.date.max = C.maxDate;
  function setDate(v) { st.date = v; el.date.value = v; el.dateView.textContent = fmtDate(v); }
  el.date.addEventListener('change', () => {
    if (!el.date.value || el.date.value < C.today || el.date.value > C.maxDate) { setDate(st.date); return; }
    setDate(el.date.value); st.start = null; refreshSlots(); update();
  });

  /* ---- ruangan & unit ---- */
  el.room.addEventListener('change', () => { fillUnits(el.room.value); st.start = null; refreshSlots(); update(); });
  el.unit.addEventListener('change', () => { fillUnits(el.room.value, +el.unit.value); st.start = null; refreshSlots(); update(); });

  /* ---- jam mulai ---- */
  async function refreshSlots(open = false) {
    if (!st.duration || !st.unit) { st.slots = []; render(); return; }
    try {
      const q = new URLSearchParams({unit:st.unit, date:st.date, duration:st.duration});
      const res = await fetch('/api/slots?' + q);
      st.slots = (await res.json()).slots || [];
    } catch { st.slots = []; toast('Gagal memuat jam. Coba lagi.'); }
    if (st.start !== null && !(st.slots.find(s => s.hour === st.start) || {}).available) st.start = null;
    if (open && st.start === null) toggle(true);
    render(); update();
  }
  function render() {
    if (!st.duration) { toggle(false); el.slots.replaceChildren(); return; }
    const nodes = st.slots.map(s => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'slot'; b.dataset.h = s.hour; b.textContent = s.label;
      b.disabled = !s.available; b.setAttribute('aria-pressed', st.start === s.hour); return b;
    });
    if (st.slots.length && !st.slots.some(s => s.available)) {
      const p = document.createElement('p'); p.className = 'slots__empty';
      p.textContent = 'Semua jam penuh untuk pilihan ini. Coba tanggal atau unit lain.'; nodes.unshift(p);
    }
    el.slots.replaceChildren(...nodes);
  }
  function toggle(open) { el.slots.hidden = !open; el.startBtn.setAttribute('aria-expanded', open); }
  el.startBtn.addEventListener('click', () => { if (!st.duration) return toast('Pilih durasi dulu.'); toggle(el.slots.hidden); });
  el.slots.addEventListener('click', (e) => {
    const b = e.target.closest('.slot'); if (!b || b.disabled) return;
    st.start = +b.dataset.h; render(); toggle(false); update();
  });

  /* ---- ringkasan ---- */
  function update() {
    const u = unitById(st.unit), ready = st.duration && st.start !== null;
    el.startText.textContent = !st.duration ? 'Pilih durasi dulu' : st.start === null ? 'Pilih jam mulai' : `${pad(st.start)} – ${pad(st.start + st.duration)}`;
    const total = u && st.duration ? u.price * st.duration : null;
    const rows = [['Ruangan', roomName(el.room.value)], ['Unit', u ? `${u.label} (${u.console})` : '—'],
      ['Tanggal', fmtDate(st.date)], ['Jam', ready ? `${pad(st.start)} – ${pad(st.start + st.duration)} (${st.duration} jam)` : '—'],
      ['Total harga', total !== null ? rupiah(total) : '—']];
    el.summary.replaceChildren(...rows.map(([k, v], i) => {
      const d = document.createElement('div'), dt = document.createElement('dt'), dd = document.createElement('dd');
      if (i === rows.length - 1) d.className = 'total';
      dt.textContent = k; dd.textContent = v; d.append(dt, dd); return d;
    }));
    el.payTotal.textContent = total !== null ? rupiah(total) : '—';
  }

  /* ---- bukti bayar ---- */
  el.proof.addEventListener('change', () => {
    const f = el.proof.files[0];
    showErr('proof', '');
    if (f && f.size > C.maxProofMb * 1024 * 1024) { el.proof.value = ''; showErr('proof', `Ukuran file maksimal ${C.maxProofMb} MB.`); }
    el.proofName.textContent = el.proof.files[0] ? el.proof.files[0].name : `Pilih file (JPG, PNG, WEBP, PDF · maks ${C.maxProofMb} MB)`;
  });

  /* ---- submit ---- */
  const showErr = (id, msg) => { const n = $('#err-' + id); n.textContent = msg || ''; n.hidden = !msg; };
  $('#rsv').addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = $('#name').value.trim(), phone = $('#phone').value.replace(/\D/g, '').replace(/^(62|0)/, '');
    showErr('name', name.length < 2 ? 'Isi nama lengkap.' : '');
    showErr('phone', /^8\d{8,12}$/.test(phone) ? '' : 'Nomor WhatsApp tidak valid. Contoh: 81234567890');
    if (name.length < 2) return $('#name').focus();
    if (!/^8\d{8,12}$/.test(phone)) return $('#phone').focus();
    if (!st.unit) return toast('Pilih unit.');
    if (!st.duration) return toast('Pilih durasi bermain.');
    if (st.start === null) { toggle(true); return toast('Pilih jam mulai.'); }
    const file = el.proof.files[0];
    if (C.proofRequired && !file) { showErr('proof', 'Bukti pembayaran wajib diunggah.'); return el.proofName.scrollIntoView({block:'center'}); }
    const fd = new FormData();
    fd.append('_csrf', C.csrf); fd.append('name', name); fd.append('phone', phone); fd.append('unit', st.unit);
    fd.append('date', st.date); fd.append('duration', st.duration); fd.append('start', st.start);
    if (file) fd.append('proof', file);
    const btn = $('#submit'); btn.disabled = true; btn.textContent = 'Mengirim…';
    try {
      const res = await fetch('/api/reservasi', {method:'POST', body:fd});
      const data = await res.json();
      if (!res.ok) { toast(data.error || 'Gagal mengirim reservasi.'); if (res.status === 409) { st.start = null; refreshSlots(true); } }
      else { showDone(data.booking); return; }
    } catch { toast('Koneksi bermasalah. Coba lagi.'); }
    btn.disabled = false; btn.textContent = 'Kirim Reservasi';
  });

  function showDone(b) {
    const rows = [['Nama', b.name], ['WhatsApp', b.phone], ['Ruangan', b.room], ['Unit', b.unit], ['Tanggal', fmtDate(b.date)],
                  ['Jam', `${b.start} – ${b.end} (${b.duration} jam)`], ['Total', rupiah(b.total)]];
    $('#doneCode').textContent = b.code;
    $('#doneSummary').replaceChildren(...rows.map(([k, v]) => {
      const d = document.createElement('div'), dt = document.createElement('dt'), dd = document.createElement('dd');
      dt.textContent = k; dd.textContent = v; d.append(dt, dd); return d;
    }));
    const msg = `Halo ${C.brand}, saya sudah reservasi ${b.code}:\n` + rows.map(([k, v]) => `${k}: ${v}`).join('\n') + '\nMohon dikonfirmasi.';
    $('#doneWa').href = `https://wa.me/${C.adminWa}?text=${encodeURIComponent(msg)}`;
    $('#rsv').hidden = true; $('#done').hidden = false; $('#done').focus();
    $('#done').scrollIntoView({behavior:'smooth', block:'start'});
  }
  $('#again').addEventListener('click', () => { window.location.hash = '#reservasi'; window.location.reload(); });

  let t;
  function toast(msg) { el.toast.textContent = msg; el.toast.hidden = false; clearTimeout(t); t = setTimeout(() => el.toast.hidden = true, 3500); }

  /* ---- pilih unit dari katalog ---- */
  function pickUnit(id) {
    const u = unitById(id); if (!u) return;
    el.room.value = u.room; fillUnits(u.room, u.id); st.start = null; refreshSlots(); update();
    document.getElementById('reservasi').scrollIntoView({behavior:'smooth'});
  }
  document.addEventListener('click', (e) => { const b = e.target.closest('.js-pick'); if (b) pickUnit(+b.dataset.unit); });

  /* ---- inisialisasi ---- */
  fillUnits(el.room.value);
  setDate(C.today);
  update();
})();
