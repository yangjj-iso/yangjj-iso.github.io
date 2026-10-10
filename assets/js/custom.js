/**
 * Main JS file for Scriptor behaviours
 */

// Responsive video embeds
let videoEmbeds = [
  'iframe[src*="youtube.com"]',
  'iframe[src*="vimeo.com"]'
];
reframe(videoEmbeds.join(','));

// Menu on small screens
let menuToggle = document.querySelectorAll('.menu-toggle');
if (menuToggle) {
  for (let i = 0; i < menuToggle.length; i++) {
    menuToggle[i].addEventListener('click', function (e) {
      document.body.classList.toggle('menu--opened');
      e.preventDefault();
    }, false);
  }
}

// Floating back-to-top button with reading progress ring
(function () {
  let button = document.getElementById('back-to-top');
  if (!button) return;
  let progress = button.querySelector('.back-to-top__progress');
  let length = progress ? progress.getTotalLength() : 0;
  if (progress) {
    progress.style.strokeDasharray = length;
    progress.style.strokeDashoffset = length;
  }
  let ticking = false;

  function update() {
    let top = window.scrollY || document.documentElement.scrollTop;
    let max = document.documentElement.scrollHeight - window.innerHeight;
    button.classList.toggle('is-visible', top > 360);
    if (progress && max > 0) {
      progress.style.strokeDashoffset = length * (1 - Math.min(top / max, 1));
    }
    ticking = false;
  }

  window.addEventListener('scroll', function () {
    if (!ticking) {
      ticking = true;
      window.requestAnimationFrame(update);
    }
  }, { passive: true });
  window.addEventListener('resize', update);
  update();

  button.addEventListener('click', function (e) {
    e.preventDefault();
    let reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    window.scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' });
    button.blur();
  });
})();
