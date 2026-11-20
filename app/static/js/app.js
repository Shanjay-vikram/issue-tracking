(function () {
  var toggle = document.getElementById('nav-toggle');
  var side = document.getElementById('sidebar');
  if (toggle && side) toggle.addEventListener('click', function () { side.classList.toggle('open'); });
  document.querySelectorAll('form[data-confirm]').forEach(function (f) {
    f.addEventListener('submit', function (e) { if (!window.confirm(f.dataset.confirm)) e.preventDefault(); });
  });
})();
// Open the tab named in the URL hash (e.g. #evidence after an upload).
(function () {
  var hash = window.location.hash;
  if (!hash || typeof bootstrap === 'undefined') return;
  var trigger = document.querySelector('[data-bs-toggle="tab"][data-bs-target="' + hash.replace(/[^#\w-]/g, '') + '"]');
  if (trigger) bootstrap.Tab.getOrCreateInstance(trigger).show();
})();
