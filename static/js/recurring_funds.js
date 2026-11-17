/* The funds of a recurring transfer. A "3 funds" link ([data-fund-open="<id>"]) opens them in a modal on a computer
   and in a bottom sheet on a phone. The rows are written once per schedule, in a <template data-fund-content="<id>">
   next to the link, so opening needs no request. */
(function () {
  'use strict';

  function open(id) {
    var template = document.querySelector('template[data-fund-content="' + id + '"]');
    if (!template || !window.bootstrap) return;
    var wide = window.matchMedia('(min-width: 768px)').matches;
    var shell = document.getElementById(wide ? 'fundSplitsModal' : 'fundSplitsSheet');
    if (!shell) return;
    shell.querySelector('[data-fund-title]').textContent = template.getAttribute('data-title') || '';
    shell.querySelector('[data-fund-body]').replaceChildren(template.content.cloneNode(true));
    var Component = wide ? window.bootstrap.Modal : window.bootstrap.Offcanvas;
    Component.getOrCreateInstance(shell).show();
  }

  document.addEventListener('click', function (event) {
    var link = event.target.closest('[data-fund-open]');
    if (!link) return;
    event.preventDefault();
    open(link.getAttribute('data-fund-open'));
  });
})();
