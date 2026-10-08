/* Global bits of the New Expense flow, loaded on every signed-in page (kept small):
 * the "Added ... Undo / Edit" toast (also restored after the post-add redirect) and the N shortcut.
 * The form itself is expense_composer.js, loaded only on /expenses/add/.
 */
(function () {
  'use strict';

  var root = document.getElementById('tmr-composer');   // present on /expenses/add/ only
  var meta = document.getElementById('tmr-c-i18n');
  if (!meta) return;

  var $ = function (id) { return document.getElementById(id); };
  var I = {};
  meta.querySelectorAll('[data-k]').forEach(function (el) { I[el.dataset.k] = el.textContent; });
  function t(key, vars) {
    var s = I[key] != null ? I[key] : key;
    if (vars) Object.keys(vars).forEach(function (k) { s = s.split('{' + k + '}').join(vars[k]); });
    return s;
  }

  var URLS = { addPath: meta.dataset.addPath };
  var CSRF = meta.dataset.csrf;
  var TOAST_KEY = 'tmr_composer_toast';
  var TOAST_MS = 6000;

  var toastsEl = $('tmr-toasts');
  // Pages other than the form just reload after an undo; the form page swaps these out below.
  var hooks = {
    nextUrl: function () { return window.location.pathname + window.location.search; },
    afterUndo: function () { setTimeout(function () { window.location.reload(); }, 600); }
  };

  // ── Toasts ────────────────────────────────────────────────────────────────
  function showToast(opts, ms) {
    var box = document.createElement('div');
    box.className = 'tmr-toast';
    var msg = document.createElement('span');
    msg.className = 'tmr-toast__msg';
    msg.textContent = opts.plain || t('addedToast', { summary: opts.summary });
    msg.title = msg.textContent;      // full text on hover when it is cut with an ellipsis
    box.appendChild(msg);
    var timer;
    function dismiss() { clearTimeout(timer); if (box.parentNode) box.parentNode.removeChild(box); }
    function arm(left) { clearTimeout(timer); timer = setTimeout(dismiss, left); }
    if (opts.undoUrl) {
      var u = document.createElement('button');
      u.type = 'button'; u.className = 'tmr-toast__btn'; u.textContent = t('undo');
      u.addEventListener('click', function () { undo(opts, box, dismiss); });
      box.appendChild(u);
    }
    if (opts.editUrl) {
      var e = document.createElement('a');
      e.className = 'tmr-toast__btn'; e.textContent = t('edit');
      var next = hooks.nextUrl();
      e.href = opts.editUrl + (opts.editUrl.indexOf('?') < 0 ? '?' : '&') + 'next=' + encodeURIComponent(next);
      box.appendChild(e);
    }
    box.addEventListener('mouseenter', function () { clearTimeout(timer); });
    box.addEventListener('mouseleave', function () { arm(2500); });
    box.addEventListener('focusin', function () { clearTimeout(timer); });
    box.addEventListener('focusout', function () { arm(2500); });
    toastsEl.appendChild(box);
    arm(ms || TOAST_MS);
  }

  function undo(opts, box, dismiss) {
    var btn = box.querySelector('button');
    if (btn) btn.disabled = true;
    fetch(opts.undoUrl, { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': CSRF } })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        dismiss();
        if (!res.ok || !res.j.success) { showToast({ plain: (res.j && res.j.error) || t('undoFailed') }, 5000); return; }
        showToast({ plain: t('undone') }, 3000);
        hooks.afterUndo(res.j);
      })
      .catch(function () { if (btn) btn.disabled = false; showToast({ plain: t('undoFailed') }, 5000); });
  }

  // Toast that survives the post-add page refresh.
  try {
    var saved = JSON.parse(sessionStorage.getItem(TOAST_KEY) || 'null');
    sessionStorage.removeItem(TOAST_KEY);
    if (saved && saved.until - Date.now() > 800) showToast(saved.toast, saved.until - Date.now());
  } catch (e) { /* ignore */ }


  // "N" opens New Expense from anywhere (desktop), never while typing or with a dialog open.
  document.addEventListener('keydown', function (e) {
    if (root || e.defaultPrevented) return;
    if (e.key !== 'n' && e.key !== 'N') return;
    if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
    var a = document.activeElement, tag = a && a.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (a && a.isContentEditable)) return;
    if (document.querySelector('.modal.show, .offcanvas.show')) return;
    e.preventDefault();
    window.location.assign(URLS.addPath);
  });

  window.TMRToast = {
    I: I, t: t, CSRF: CSRF, URLS: URLS, showToast: showToast, hooks: hooks,
    TOAST_MS: TOAST_MS, TOAST_KEY: TOAST_KEY
  };
})();
