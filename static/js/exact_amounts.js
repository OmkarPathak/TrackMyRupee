/* The exact amount behind a rounded one.

   An amount that is shown rounded or abbreviated (2.3L, 1.2Cr, 14,792 for 14,791.67) is written by the template as
   <span class="tmr-amt" data-exact="₹2,31,233">: a tap or click on it shows the exact amount in a small tooltip.
   Only one is open at a time. It closes on the next click or tap elsewhere, on Escape, when the page scrolls or
   changes, or after a few seconds. A tap on an amount inside a link or a card does not follow it: it shows the amount. */
(function () {
  'use strict';

  var SHOW_MS = 4000;
  var openEl = null;
  var timer = null;

  function tooltipFor(el) {
    if (!window.bootstrap || !window.bootstrap.Tooltip) return null;
    return window.bootstrap.Tooltip.getOrCreateInstance(el, {
      trigger: 'manual',
      title: el.getAttribute('data-exact') || '',
      placement: 'top',
      container: 'body',
      customClass: 'tmr-amt-tip',
      fallbackPlacements: ['bottom', 'top']
    });
  }

  function close() {
    if (timer) { clearTimeout(timer); timer = null; }
    if (openEl) {
      var tip = window.bootstrap && window.bootstrap.Tooltip.getInstance(openEl);
      if (tip) tip.hide();
      openEl = null;
    }
  }

  function show(el) {
    close();
    var tip = tooltipFor(el);
    if (!tip) return;
    tip.show();
    openEl = el;
    timer = setTimeout(close, SHOW_MS);
  }

  function toggle(el) {
    if (openEl === el) { close(); } else { show(el); }
  }

  // Capture phase: an amount inside a link or a clickable card must not trigger it
  document.addEventListener('click', function (event) {
    var el = event.target.closest && event.target.closest('.tmr-amt');
    if (!el) { close(); return; }
    event.preventDefault();
    event.stopPropagation();
    toggle(el);
  }, true);

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') { close(); return; }
    if (event.key !== 'Enter' && event.key !== ' ') return;
    var el = event.target.closest && event.target.closest('.tmr-amt');
    if (!el) return;
    event.preventDefault();
    toggle(el);
  });

  window.addEventListener('scroll', close, true);
  window.addEventListener('resize', close);
  document.addEventListener('htmx:beforeSwap', close);
})();
