(() => {
  const nav = document.getElementById('nav'), btn = document.getElementById('navToggle');
  if (btn) btn.addEventListener('click', () => {
    const open = nav.classList.toggle('open');
    btn.setAttribute('aria-expanded', open);
  });
  // tandai menu sesuai bagian yang sedang dilihat
  const links = [...document.querySelectorAll('.nav a[href*="#"]')];
  const secs = ['beranda','unit','reservasi','kontak'].map(id => document.getElementById(id)).filter(Boolean);
  if (links.length && 'IntersectionObserver' in window) {
    const io = new IntersectionObserver((es) => es.forEach(e => {
      if (e.isIntersecting) links.forEach(a => a.classList.toggle('on', a.hash === '#' + e.target.id));
    }), {rootMargin:'-45% 0px -50% 0px'});
    secs.forEach(s => io.observe(s));
  }
  document.querySelectorAll('.flash').forEach(f => setTimeout(() => f.remove(), 8000));
})();
