/* Guide links ([data-docs-path], with data-docs-src saying where the link sits): counts how often each one is used, and
   marks the visit so the guide's own analytics can tell it came from the app.
   - A PostHog event (docs_link_clicked: the guide page and where the link sits, nothing about the person), only after
     the visitor accepted analytics, like every other event.
   - The address gets utm_source/utm_medium/utm_content, so the guide site sees "from the app, from the Budgets page"
     rather than an unnamed visit. (The guide's canonical addresses carry no parameters, so search is unaffected.) */
(function () {
  'use strict';

  function consented() {
    try { return localStorage.getItem('cookieConsent') === 'accepted'; } catch (e) { return false; }
  }

  function mark(link, source) {
    try {
      var url = new URL(link.href);
      if (url.searchParams.has('utm_source')) return;
      url.searchParams.set('utm_source', 'trackmyrupee-app');
      url.searchParams.set('utm_medium', 'guide-link');
      if (source) url.searchParams.set('utm_content', source);
      link.href = url.toString();
    } catch (e) { /* leave the link as it is */ }
  }

  document.addEventListener('click', function (event) {
    var link = event.target.closest('a[data-docs-path]');
    if (!link) return;
    var page = link.getAttribute('data-docs-path') || '';
    var source = link.getAttribute('data-docs-src') || '';
    if (consented() && window.posthog && typeof window.posthog.capture === 'function') {
      try { window.posthog.capture('docs_link_clicked', { page: page, source: source }); } catch (e) { /* never worth an error */ }
    }
    mark(link, source);
  }, true);
})();
