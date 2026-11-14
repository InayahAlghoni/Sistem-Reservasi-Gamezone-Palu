(() => {
  const CFG = window.GZ;
  const $ = (s) => document.querySelector(s);
  const rupiah = (n) => new Intl.NumberFormat('id-ID', {style:'currency', currency:'IDR', maximumFractionDigits:0}).format(n);
  const fmtDate = (iso) => new Intl.DateTimeFormat('id-ID', {weekday:'long', day:'numeric', month:'long', year:'numeric'}).format(new Date(iso + 'T00:00:00'));
  const ICONS = {vip:'i-star', reguler:'i-pad', smoking:'i-ball'};

  const state = {room:CFG.rooms[0].id, date:CFG.today, duration:null, start:null, slots:[]};
  const el = {
    rooms:$('#rooms'), durations:$('#durations'), date:$('#date'), dateView:$('#dateView'),
    startBtn:$('#startBtn'), startText:$('#startText'), slots:$('#slots'),
    total:$('#total'), next:$('#next'), toast:$('#toast'),
  };
  const svg = (id) => `<svg><use href="#${id}"/></svg>`;

  /* ---------- render ruangan ---------- */
  function renderRooms() {
    el.rooms.innerHTML = CFG.rooms.map(r => `<button type="button" class="room room--${r.theme}" data-room="${r.id}" aria-pressed="${r.id === state.room}"${r.image ? ` style="--img:url('${r.image}')"` : ''}>
      <span class="room__ico">${svg(ICONS[r.id] || 'i-pad')}</span>
      <span class="room__t display">${r.name}</span>
      <span class="room__p">${rupiah(r.price)}/jam</span>
      <span class="room__d">${r.features}</span>
      <span class="room__check">${svg('i-check')}</span></button>`).join('');
    el.rooms.addEventListener('click', (e) => {
      const b = e.target.closest('.room'); if (!b) return;
      state.room = b.dataset.room;
      el.rooms.querySelectorAll('.room').forEach(x => x.setAttribute('aria-pressed', x === b));
      state.start = null; refreshSlots(); updateUI();
    });
  }

  function renderDurations() {
    el.durations.innerHTML = CFG.durations.map(d =>
      `<button type="button" class="dur display" data-d="${d}" aria-pressed="false">${d} jam</button>`).join('');
    el.durations.addEventListener('click', (e) => {
      const b = e.target.closest('.dur'); if (!b) return;
      state.duration = +b.dataset.d; state.start = null;
      el.durations.querySelectorAll('.dur').forEach(x => x.setAttribute('aria-pressed', x === b));
      refreshSlots(true); updateUI();
    });
  }

  /* ---------- tanggal ---------- */
  function initDate() {
    el.date.min = CFG.today; el.date.max = CFG.maxDate; el.date.value = state.date;
    el.dateView.textContent = fmtDate(state.date);
    el.date.addEventListener('change', () => {
      if (!el.date.value || el.date.value < CFG.today || el.date.value > CFG.maxDate) { el.date.value = state.date; return; }
      state.date = el.date.value; state.start = null;
      el.dateView.textContent = fmtDate(state.date);
      refreshSlots(); updateUI();
    });
  }

  /* ---------- slot jam ---------- */
  async function refreshSlots(open = false) {
    if (!state.duration) { state.slots = []; renderSlots(); return; }
    try {
      const q = new URLSearchParams({date:state.date, room:state.room, duration:state.duration});
      const res = await fetch('/api/slots?' + q);
      const data = await res.json();
      state.slots = data.slots || [];
    } catch { state.slots = []; toast('Gagal memuat jam. Coba lagi.'); }
    if (open) toggleSlots(true);
    renderSlots(); updateUI();
  }

  function renderSlots() {
    if (!state.duration) { toggleSlots(false); return; }
    const free = state.slots.filter(s => s.available);
    el.slots.innerHTML = free.length || state.slots.length
      ? state.slots.map(s => `<button type="button" class="slot" data-h="${s.hour}" ${s.available ? '' : 'disabled'} aria-pressed="${state.start === s.hour}">${s.label}</button>`).join('')
      : '';
    if (state.slots.length && !free.length)
      el.slots.insertAdjacentHTML('afterbegin', '<p class="slots__empty">Semua jam penuh untuk pilihan ini. Coba tanggal atau ruangan lain.</p>');
  }

  function toggleSlots(open) {
    el.slots.hidden = !open;
    el.startBtn.setAttribute('aria-expanded', open);
  }

  el.startBtn.addEventListener('click', () => {
    if (!state.duration) { toast('Pilih durasi dulu.'); return; }
    toggleSlots(el.slots.hidden);
  });
  el.slots.addEventListener('click', (e) => {
    const b = e.target.closest('.slot'); if (!b || b.disabled) return;
    state.start = +b.dataset.h; renderSlots(); toggleSlots(false); updateUI();
  });

  /* ---------- total & status ---------- */
  function price() { return CFG.rooms.find(r => r.id === state.room).price; }
  const pad = (h) => String(h % 24).padStart(2, '0') + ':00';

  function updateUI() {
    const ready = !!(state.duration && state.start !== null);
    el.total.textContent = state.duration ? rupiah(price() * state.duration) : '—';
    el.next.classList.toggle('ready', ready);
    el.startBtn.classList.toggle('ready', ready);
    el.startBtn.querySelector('.warn use').setAttribute('href', ready ? '#i-check' : '#i-warn');
    el.startText.textContent = !state.duration ? 'Pilih durasi dulu'
      : state.start === null ? 'Pilih jam mulai'
      : `${pad(state.start)} – ${pad(state.start + state.duration)}`;
  }

  /* ---------- validasi & submit ---------- */
  const showErr = (id, msg) => { const n = $('#err-' + id); n.textContent = msg || ''; n.hidden = !msg; };
  function validate() {
    const name = $('#name').value.trim();
    const phone = $('#phone').value.replace(/\D/g, '').replace(/^(62|0)/, '');
    showErr('name', name.length < 2 ? 'Isi nama lengkap.' : '');
    showErr('phone', /^8\d{8,12}$/.test(phone) ? '' : 'Nomor WhatsApp tidak valid. Contoh: 81234567890');
    if (name.length < 2) { $('#name').focus(); return null; }
    if (!/^8\d{8,12}$/.test(phone)) { $('#phone').focus(); return null; }
    if (!state.duration) { toast('Pilih durasi bermain.'); return null; }
    if (state.start === null) { toast('Pilih jam mulai.'); toggleSlots(true); return null; }
    return {name, phone, room:state.room, date:state.date, duration:state.duration, start:state.start};
  }

  el.next.addEventListener('click', async () => {
    const payload = validate(); if (!payload) return;
    el.next.disabled = true;
    try {
      const res = await fetch('/api/booking', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      const data = await res.json();
      if (!res.ok) { toast(data.error || 'Gagal membuat booking.'); if (res.status === 409) { state.start = null; refreshSlots(true); } return; }
      showDone(data.booking);
    } catch { toast('Koneksi bermasalah. Coba lagi.'); }
    finally { el.next.disabled = false; }
  });

  function showDone(b) {
    $('#doneCode').textContent = b.code;
    const rows = [['Nama', b.name], ['WhatsApp', b.phone], ['Ruangan', b.room], ['Tanggal', fmtDate(b.date)],
                  ['Jam', `${b.start} – ${b.end} (${b.duration} jam)`], ['Total', rupiah(b.total)]];
    const dl = $('#doneSummary'); dl.innerHTML = '';
    rows.forEach(([k, v]) => {
      const d = document.createElement('div'), dt = document.createElement('dt'), dd = document.createElement('dd');
      dt.textContent = k; dd.textContent = v; d.append(dt, dd); dl.append(d);
    });
    const msg = `Halo ${CFG.brand}, saya ingin konfirmasi booking ${b.code}:\n` +
      rows.map(([k, v]) => `${k}: ${v}`).join('\n');
    $('#doneWa').href = `https://wa.me/${CFG.adminWa}?text=${encodeURIComponent(msg)}`;
    $('#doneWa').textContent = 'Konfirmasi via WhatsApp';
    openModal('doneModal');
  }

  /* ---------- modal, menu, toast ---------- */
  function openModal(id) { $('#' + id).hidden = false; document.body.style.overflow = 'hidden'; }
  function closeModals() { document.querySelectorAll('.modal').forEach(m => m.hidden = true); document.body.style.overflow = ''; }
  document.querySelectorAll('.modal').forEach(m => m.addEventListener('click', (e) => {
    if (e.target === m || e.target.closest('[data-close]')) closeModals();
  }));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closeModals(); closeMenu(); } });

  $('#compareList').innerHTML = CFG.rooms.map(r =>
    `<div class="cmp"><div class="cmp__h"><strong class="display">${r.name}</strong><span class="cmp__p">${rupiah(r.price)}/jam</span></div><p>${r.features}</p></div>`).join('');
  $('#openCompare').addEventListener('click', () => openModal('compareModal'));
  $('#menuCompare').addEventListener('click', () => { closeMenu(); openModal('compareModal'); });

  const menuBtn = $('#menuBtn'), menuList = $('#menuList');
  function closeMenu() { menuList.hidden = true; menuBtn.setAttribute('aria-expanded', 'false'); }
  menuBtn.addEventListener('click', (e) => { e.stopPropagation(); menuList.hidden = !menuList.hidden; menuBtn.setAttribute('aria-expanded', !menuList.hidden); });
  document.addEventListener('click', (e) => { if (!e.target.closest('.menu')) closeMenu(); });
  menuList.addEventListener('click', (e) => { if (e.target.closest('a')) closeMenu(); });

  let toastTimer;
  function toast(msg) {
    el.toast.textContent = msg; el.toast.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => el.toast.hidden = true, 3200);
  }

  /* nama brand muncul di header saat judul hero keluar layar */
  new IntersectionObserver(([en]) => $('#topbarBrand').classList.toggle('show', !en.isIntersecting),
    {rootMargin:'-70px 0px 0px 0px'}).observe($('.logo-slot'));

  renderRooms(); renderDurations(); initDate(); updateUI();
})();
