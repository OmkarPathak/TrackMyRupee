/* New Expense form: type one line, review six fields, add.
 * Markup: templates/components/expense_composer_card.html   Styles: static/css/expense_composer.css
 * Loaded only on /expenses/add/ (the toast and the N shortcut are expense_toast.js, loaded everywhere).
 * Endpoints: parse-expense (dry run), expense-composer-{data,save,event}, expense-composer-undo.
 */
(function () {
  'use strict';

  var T = window.TMRToast;
  var root = document.getElementById('tmr-composer');
  var meta = document.getElementById('tmr-c-i18n-page');
  if (!T || !root || !meta) return;

  var $ = function (id) { return document.getElementById(id); };
  meta.querySelectorAll('[data-k]').forEach(function (el) { T.I[el.dataset.k] = el.textContent; });
  var t = T.t, CSRF = T.CSRF, showToast = T.showToast, hooks = T.hooks;
  var TOAST_MS = T.TOAST_MS, TOAST_KEY = T.TOAST_KEY;
  var URLS = {
    parse: meta.dataset.parseUrl,
    data: meta.dataset.dataUrl,
    save: meta.dataset.saveUrl,
    event: meta.dataset.eventUrl,
    category: meta.dataset.categoryUrl,
    capital: meta.dataset.capitalUrl,
    addPath: T.URLS.addPath,
    pricing: meta.dataset.pricingUrl
  };
  var LANG = (document.documentElement.lang || 'en').slice(0, 2).toLowerCase();
  var LOCALE = { hi: 'hi-IN', mr: 'mr-IN' }[LANG] || 'en-IN';


  // Field -> analytics/server name; also the Enter-to-next order (currency is Tab-only).
  var FIELDS = ['amount', 'date', 'description', 'category', 'account', 'payment'];
  var EVENT_FIELD = {
    amount: 'amount', currency: 'currency', date: 'date', description: 'description',
    category: 'category', account: 'account', payment: 'payment_method'
  };

  // ── Elements ──────────────────────────────────────────────────────────────
  var el = {
    quick: $('tmr-quick-input'), go: $('tmr-go'), mic: $('tmr-mic'),
    dateNote: $('tmr-datenote'), dateNoteText: $('tmr-datenote-text'), dateNoteReset: $('tmr-datenote-reset'),
    voiceMsg: $('tmr-voice-msg'),
    stateA: $('tmr-state-a'), stateB: $('tmr-state-b'), usual: $('tmr-usual'), manual: $('tmr-manual'),
    bHelp: $('tmr-b-help'), bNotice: $('tmr-b-notice'), bError: $('tmr-b-error'),
    bErrorText: $('tmr-b-error-text'), bErrorActions: $('tmr-b-error-actions'),
    amount: $('tmr-amount'), currency: $('tmr-currency'), amountMsg: $('tmr-amount-msg'),
    dateGroup: $('tmr-date-group'), dateInput: $('tmr-date-input'), dateCustom: $('tmr-date-custom'),
    pickFace: $('tmr-pick-face'), dateMsg: $('tmr-date-msg'),
    description: $('tmr-description'),
    category: $('tmr-category'), categoryMsg: $('tmr-category-msg'),
    catInline: $('tmr-cat-inline'), catName: $('tmr-cat-name'), catSave: $('tmr-cat-save'),
    catCancel: $('tmr-cat-cancel'), catMsg: $('tmr-cat-msg'),
    account: $('tmr-account'),
    payGroup: $('tmr-pay-group'), payMsg: $('tmr-pay-msg'),
    foot: $('tmr-foot'), cancel: $('tmr-cancel'), addAnother: $('tmr-add-another'), add: $('tmr-add'),
    cLarge: $('tmr-confirm-large'), largeText: $('tmr-large-text'), largeYes: $('tmr-large-yes'),
    largeEdit: $('tmr-large-edit'), largeCapital: $('tmr-large-capital'),
    live: $('tmr-live')
  };
  var fieldEl = {};
  root.querySelectorAll('[data-field]').forEach(function (n) { fieldEl[n.dataset.field] = n; });
  var addHtml = el.add.innerHTML;
  var pickLabel = el.pickFace.textContent;

  // ── State ─────────────────────────────────────────────────────────────────
  var S = {
    open: false, view: 'A', data: null, loading: null,
    v: { amount: '', currency: '₹', date: '', description: '', category: '', accountId: '', payment: 'Cash' },
    origin: {}, tag: {}, check: {}, edited: {}, usedParse: false,
    key: '', openedAt: 0, entry: 'other', stickyDate: null, saving: false,
    opener: null, parseSeq: 0, largeOk: false, pushed: false, lastAnother: false,
    deepLink: false, nextUrl: '', prevCategory: ''
  };

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = Math.random() * 16 | 0;
      return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
    });
  }
  function live(text) {
    el.live.textContent = '';
    setTimeout(function () { el.live.textContent = text; }, 40);
  }
  function show(node, on) { node.hidden = !on; }

  // ── Dates ─────────────────────────────────────────────────────────────────
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function isoOf(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function todayISO() { return (S.data && S.data.today) || isoOf(new Date()); }
  function addDays(iso, n) {
    var p = iso.split('-');
    var d = new Date(+p[0], +p[1] - 1, +p[2] + n);
    return isoOf(d);
  }
  function fmtDate(iso) {
    var p = iso.split('-');
    var d = new Date(+p[0], +p[1] - 1, +p[2]);
    var opts = { day: 'numeric', month: 'short' };
    if (d.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
    try { return d.toLocaleDateString(LOCALE, opts); } catch (e) { return iso; }
  }
  function dateKind(iso) {
    if (iso === todayISO()) return 'today';
    if (iso === addDays(todayISO(), -1)) return 'yesterday';
    return 'custom';
  }
  function validISO(s) { return /^\d{4}-\d{2}-\d{2}$/.test(s || '') && !isNaN(new Date(s + 'T00:00:00')); }

  // ── Amount formatting (Indian grouping for rupees) ────────────────────────
  function sanitizeAmount(str) {
    var out = '', dot = false, dec = 0;
    for (var i = 0; i < str.length; i++) {
      var ch = str[i];
      if (ch >= '0' && ch <= '9') {
        if (dot) { if (dec >= 2) continue; dec++; }
        out += ch;
      } else if (ch === '.' && !dot) { dot = true; out += '.'; }
    }
    var parts = out.split('.');
    parts[0] = parts[0].replace(/^0+(?=\d)/, '').slice(0, 12);
    if (out.charAt(0) === '.') parts[0] = '0';
    return parts.length > 1 ? parts[0] + '.' + parts[1] : parts[0];
  }
  function groupInt(digits, cur) {
    if (digits.length <= 3) return digits;
    if (cur === '₹') {
      return digits.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ',') + ',' + digits.slice(-3);
    }
    return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }
  function formatAmount(raw, cur) {
    if (!raw) return '';
    var p = raw.split('.');
    return groupInt(p[0], cur) + (p.length > 1 ? '.' + p[1] : '');
  }
  function amountNumber() { var n = parseFloat(S.v.amount); return isNaN(n) ? 0 : n; }
  function symbolFmt(n, cur) {
    return cur + formatAmount(String(Math.round(n * 100) / 100), cur);
  }

  // ── Fields: painting state onto the DOM ───────────────────────────────────
  var TAG_TEXT = { auto: 'tagAuto', lastused: 'tagLastUsed', today: 'tagToday', check: 'tagCheck' };

  function paintTag(f) {
    var wrap = fieldEl[f];
    if (!wrap) return;
    var tag = S.tag[f];
    var node = wrap.querySelector('[data-tag]');
    wrap.dataset.origin = S.origin[f] || 'default';
    wrap.classList.toggle('is-check', tag === 'check');
    if (!tag) { node.hidden = true; node.textContent = ''; return; }
    node.hidden = false;
    node.className = 'tmr-tag' + (tag === 'check' ? ' tmr-tag--check' : (tag === 'lastused' || tag === 'today' ? ' tmr-tag--neutral' : ''));
    node.textContent = '';
    if (tag === 'check') {
      var i = document.createElement('i');
      i.className = 'bi bi-exclamation-triangle-fill';
      i.setAttribute('aria-hidden', 'true');
      node.appendChild(i);
    }
    node.appendChild(document.createTextNode(t(TAG_TEXT[tag])));
  }

  // The app's .tmr-pill / .payment-method-item styles key off .active, so mirror the checked radio.
  function syncActive() {
    root.querySelectorAll('label.tmr-sel').forEach(function (l) {
      var r = l.querySelector('input[type=radio]');
      l.classList.toggle('active', !!(r && r.checked));
    });
  }

  function paintDate() {
    var iso = S.v.date, kind = dateKind(iso);
    el.dateGroup.querySelectorAll('input[type=radio]').forEach(function (r) { r.checked = r.value === kind; });
    el.pickFace.textContent = kind === 'custom' ? fmtDate(iso) : pickLabel;
    el.dateInput.value = iso;
    syncActive();
    var future = iso > todayISO();
    el.dateMsg.hidden = !future;
    el.dateMsg.textContent = future ? t('futureDate') : '';
  }

  function paint() {
    el.currency.value = S.v.currency;
    el.amount.value = formatAmount(S.v.amount, S.v.currency);
    el.description.value = S.v.description;
    el.category.value = S.v.category && optionExists(el.category, S.v.category) ? S.v.category : '';
    el.account.value = S.v.accountId && optionExists(el.account, String(S.v.accountId)) ? String(S.v.accountId) : '';
    [el.currency, el.category, el.account].forEach(refreshSelect);
    el.payGroup.querySelectorAll('input').forEach(function (r) { r.checked = r.value === S.v.payment; });
    syncActive();
    paintDate();
    FIELDS.forEach(paintTag);
    updateDateNote();
  }
  // The app's searchable-select component hides the real <select> and shows its own button; it must
  // be told when options or the value change from script, and focus goes to its button.
  // When a search in the category dropdown finds nothing, offer to create that category right there.
  var catWatched = false;
  function watchCategorySearch() {
    var wrap = el.category.closest('.searchable-select-wrapper');
    var list = wrap && wrap.querySelector('.searchable-select-list');
    if (!list || catWatched) return;
    catWatched = true;
    new MutationObserver(function () {
      var term = (wrap.querySelector('.searchable-select-search') || {}).value || '';
      term = term.trim();
      if (!term || !list.querySelector('.searchable-select-empty') || list.querySelector('.tmr-create-item')) return;
      var item = document.createElement('div');
      item.className = 'searchable-select-item tmr-create-item';
      item.textContent = t('createNamed', { name: term });
      item.addEventListener('click', function () {
        var dd = wrap.querySelector('.searchable-select-dropdown');
        if (dd) dd.classList.remove('show');
        wrap.classList.remove('open');
        el.catMsg.hidden = true; el.catName.value = term;
        show(el.catInline, true); el.catName.focus();
      });
      list.appendChild(item);
    }).observe(list, { childList: true });
  }

  function refreshSelect(sel) {
    if (sel === el.category) watchCategorySearch();
    if (typeof sel.searchableSelectRefresh === 'function') sel.searchableSelectRefresh();
    if (sel === el.category) {
      // "Select category" is only a placeholder on the button, never an option in the list.
      var btn = ctl(sel), empty = sel.selectedIndex < 0;
      if (empty) btn.textContent = t('selectCategory');
      btn.classList.toggle('tmr-placeholder', empty);
    }
  }
  function ctl(sel) {
    var w = sel.closest('.searchable-select-wrapper');
    return (w && w.querySelector('.searchable-select-btn')) || sel;
  }
  function optionExists(sel, value) {
    for (var i = 0; i < sel.options.length; i++) if (sel.options[i].value === value) return true;
    return false;
  }

  function populateSelects() {
    var d = S.data;
    if (!d) return;
    el.currency.textContent = '';
    d.currencies.forEach(function (c) {
      var o = new Option(c.symbol === c.code ? c.code : c.symbol + ' ' + c.code, c.symbol);
      o.title = c.label;
      el.currency.add(o);
    });
    fillCategories();
    el.account.textContent = '';
    d.accounts.forEach(function (a) { el.account.add(new Option(a.label, String(a.id))); });
    refreshSelect(el.currency); refreshSelect(el.account);
  }
  function fillCategories() {
    el.category.textContent = '';
    (S.data ? S.data.categories : []).forEach(function (c) { el.category.add(new Option(c, c)); });
    el.category.add(new Option(t('createCategory'), '__create__'));
    refreshSelect(el.category);
  }

  // ── Defaults & reset ──────────────────────────────────────────────────────
  function applyDefaults(onlyDefaults) {
    var d = S.data;
    function maybe(f, fn) { if (!onlyDefaults || !S.origin[f] || S.origin[f] === 'default') fn(); }
    maybe('currency', function () { S.v.currency = d ? d.default_currency : S.v.currency; S.origin.currency = 'default'; });
    maybe('date', function () {
      S.v.date = S.stickyDate || todayISO();
      S.origin.date = 'default';
      S.tag.date = S.stickyDate ? null : 'today';
    });
    maybe('account', function () {
      if (!d) return;
      S.v.accountId = d.default_account_id || '';
      S.origin.account = 'default';
      S.tag.account = d.default_account_source === 'last_used' ? 'lastused' : null;
    });
    maybe('payment', function () {
      if (!d) return;
      S.v.payment = d.default_payment_method || 'Cash';
      S.origin.payment = 'default';
      S.tag.payment = null;
    });
  }

  function resetCard(keepDate) {
    S.v = { amount: '', currency: S.data ? S.data.default_currency : '₹', date: '', description: '', category: '', accountId: '', payment: 'Cash' };
    S.origin = {}; S.tag = {}; S.edited = {}; S.usedParse = false;
    S.key = uuid(); S.largeOk = false; S.saving = false; S.parseSeq++;
    if (!keepDate) S.stickyDate = null;
    applyDefaults(false);
    el.quick.value = '';
    hideMessages();
    S.view = 'A';
    show(el.stateA, true); show(el.stateB, false); show(el.foot, false);
    setBusy(false);
    renderUsual();
    paint();
  }

  function hideMessages() {
    show(el.bNotice, false); show(el.bError, false); show(el.cLarge, false);
    show(el.voiceMsg, false); show(el.catInline, false);
    [el.amountMsg, el.categoryMsg, el.payMsg].forEach(function (n) { if (n) { n.hidden = true; n.textContent = ''; } });
    Object.keys(fieldEl).forEach(function (f) { fieldEl[f].classList.remove('is-invalid'); });
  }

  function updateDateNote() {
    var sticky = S.stickyDate && S.stickyDate !== todayISO();
    show(el.dateNote, !!sticky && S.view === 'A');
    if (sticky) {
      var k = dateKind(S.stickyDate);
      el.dateNoteText.textContent = t('dateNote', { date: k === 'yesterday' ? t('yesterday') : k === 'today' ? t('today') : fmtDate(S.stickyDate) });
    }
  }

  // ── Data ──────────────────────────────────────────────────────────────────
  function ensureData() {
    if (S.data) return Promise.resolve(S.data);
    if (S.loading) return S.loading;
    S.loading = fetch(URLS.data, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (r) { if (!r.ok) throw new Error('data ' + r.status); return r.json(); })
      .then(function (j) {
        S.data = j.data;
        S.loading = null;
        populateSelects();
        applyDefaults(true);
        renderUsual();
        paint();
        show(el.voiceMsg, false);
        return S.data;
      })
      .catch(function (err) { S.loading = null; throw err; });
    return S.loading;
  }
  function showLoadError() {
    showError(t('loadFailed'), [{ label: t('retry'), onClick: function () {
      show(el.bError, false);
      ensureData().catch(showLoadError);
    } }]);
  }

  // ── "Your usual" chips ────────────────────────────────────────────────────
  function renderUsual() {
    el.usual.textContent = '';
    var chips = [];
    ((S.data && S.data.usuals) || []).forEach(function (u) { chips.push({ label: u.label, usual: u }); });
    ['ex1', 'ex2', 'ex3', 'ex4'].forEach(function (k) {
      if (chips.length < 4 && !chips.some(function (c) { return c.label === t(k); })) chips.push({ label: t(k) });
    });
    chips.slice(0, 4).forEach(function (c) {
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'tmr-flow-cat'; b.textContent = c.label;
      b.addEventListener('click', function () {
        if (c.usual) fillFromUsual(c); else { el.quick.value = c.label; parse(); }
      });
      el.usual.appendChild(b);
    });
  }
  function fillFromUsual(c) {
    var u = c.usual;
    ensureData().catch(function () {}).then(function () {
      el.quick.value = c.label;
      S.usedParse = true;
      S.v.amount = String(parseFloat(u.amount));
      S.v.currency = u.currency || S.v.currency;
      S.v.description = u.description;
      S.v.category = S.data && S.data.categories.indexOf(u.category) >= 0 ? u.category : '';
      if (u.account_id && S.data && S.data.accounts.some(function (a) { return a.id === u.account_id; })) S.v.accountId = u.account_id;
      S.v.payment = u.payment_method || S.v.payment;
      S.v.date = S.stickyDate || todayISO();
      ['amount', 'currency', 'description', 'category', 'account', 'payment'].forEach(function (f) { S.origin[f] = 'usual'; S.tag[f] = null; });
      S.tag.date = S.stickyDate ? null : 'today';
      if (!S.v.category) S.tag.category = 'check';
      showB();
      paint();
      sendEvent('your_usual_chip_used', {});
      el.add.focus();
    });
  }

  // ── View switching ────────────────────────────────────────────────────────
  function showB() {
    S.view = 'B';
    show(el.stateA, false); show(el.stateB, true); show(el.foot, true);
    show(el.bHelp, S.usedParse);
    updateDateNote();
    if (!S.data) fillCategories();
  }
  function enterManual() {
    ensureData().then(function () {
      showB(); paint(); el.amount.focus();
    }).catch(function () {
      showB(); paint(); showLoadError(); el.amount.focus();
    });
  }

  // ── Parse ─────────────────────────────────────────────────────────────────
  function setGoBusy(on) { el.go.disabled = on; }
  function parse() {
    var text = el.quick.value.trim();
    if (!text) { el.quick.focus(); return; }
    var seq = ++S.parseSeq;
    setGoBusy(true);
    ensureData().catch(function () {}).then(function () {
      return fetch(URLS.parse, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
        body: JSON.stringify({ text: text, default_account_id: S.data ? S.data.default_account_id : null })
      });
    }).then(function (r) {
      if (!r.ok) throw new Error('parse ' + r.status);
      return r.json();
    }).then(function (j) {
      if (seq !== S.parseSeq) return;
      if (!j.success || !j.data) throw new Error('parse empty');
      S.usedParse = true;
      showB();
      applyParse(j.data);
    }).catch(function () {
      if (seq !== S.parseSeq) return;
      showB(); paint();
      el.bNotice.textContent = t('parseFailed'); show(el.bNotice, true);
      if (!S.data) showLoadError();
      sendEvent('quick_add_parsed', { success: false, fields_filled: 0, check_count: 0 });
      el.amount.focus();
    }).then(function () { if (seq === S.parseSeq) setGoBusy(false); });
  }

  function editable(f) { return S.origin[f] !== 'user'; }

  function applyParse(r) {
    var checks = r.check || [];
    var conf = r.confidence || {};
    show(el.bNotice, false);

    if (r.amount && editable('amount')) {
      S.v.amount = String(parseFloat(r.amount)); S.origin.amount = 'parser';
      S.tag.amount = checks.indexOf('amount') >= 0 ? 'check' : 'auto';
    } else if (!r.amount && editable('amount') && !S.v.amount) {
      S.tag.amount = 'check';
    }
    if (editable('currency')) {
      if (r.currency_found) { S.v.currency = r.currency; S.origin.currency = 'parser'; }
      else if (S.origin.currency === 'parser') { S.v.currency = S.data ? S.data.default_currency : '₹'; S.origin.currency = 'default'; }
    }
    if (editable('date')) {
      if (r.date_found) { S.v.date = r.date; S.origin.date = 'parser'; S.tag.date = 'auto'; }
      else if (S.origin.date === 'parser') { S.v.date = S.stickyDate || todayISO(); S.origin.date = 'default'; S.tag.date = S.stickyDate ? null : 'today'; }
    }
    if (editable('description')) {
      S.v.description = r.description || ''; S.origin.description = 'parser'; S.tag.description = 'auto';
    }
    if (editable('category')) {
      if (r.category) {
        S.v.category = r.category; S.origin.category = 'parser';
        S.tag.category = checks.indexOf('category') >= 0 || conf.category === 'low' ? 'check' : 'auto';
      } else { S.v.category = ''; S.origin.category = 'parser'; S.tag.category = 'check'; }
    }
    if (editable('account')) {
      if (r.account_id) {
        S.v.accountId = r.account_id; S.origin.account = 'parser';
        S.tag.account = checks.indexOf('account') >= 0 ? 'check' : 'auto';
      } else if (S.origin.account === 'parser') { applyDefaults(false); }
    }
    if (editable('payment')) {
      if (r.payment_method) {
        S.v.payment = r.payment_method; S.origin.payment = 'parser';
        S.tag.payment = checks.indexOf('payment_method') >= 0 ? 'check' : 'auto';
      } else if (r.payment_ambiguous) {
        S.v.payment = ''; S.origin.payment = 'parser'; S.tag.payment = 'check';
      } else if (S.origin.payment === 'parser') {
        S.v.payment = S.data ? S.data.default_payment_method : 'Cash'; S.origin.payment = 'default'; S.tag.payment = null;
      }
    }
    paint();

    var filled = FIELDS.filter(function (f) { return S.origin[f] === 'parser'; }).length;
    var checked = FIELDS.filter(function (f) { return S.tag[f] === 'check'; });
    var names = { amount: 'fAmount', date: 'fDate', description: 'fDescription', category: 'fCategory', account: 'fAccount', payment: 'fPayment' };
    var msg = t('filled', { n: filled });
    if (checked.length) msg += ' ' + t('checkThese', { fields: checked.map(function (f) { return t(names[f]); }).join(', ') });
    live(msg);
    sendEvent('quick_add_parsed', { success: !!r.amount, fields_filled: filled, check_count: checked.length });

    var amountOk = r.amount && S.tag.amount !== 'check';
    if (!r.amount) {
      el.bNotice.textContent = t('noAmount'); show(el.bNotice, true);
      el.amount.focus();
    } else if (amountOk) {
      el.add.focus();
    } else {
      el.amount.focus();
    }
  }

  // ── User edits ────────────────────────────────────────────────────────────
  function userEdit(f) {
    S.origin[f] = 'user';
    S.tag[f] = null;
    S.largeOk = false;
    var wrap = fieldEl[f];
    if (wrap) { wrap.classList.remove('is-invalid'); }
    paintTag(f);
    var name = EVENT_FIELD[f];
    if (!S.edited[name]) { S.edited[name] = true; sendEvent('expense_field_edited', { field: name }); }
  }

  el.amount.addEventListener('input', function () {
    var caret = el.amount.selectionStart || 0;
    var sig = 0;
    for (var i = 0; i < caret; i++) if (/[0-9.]/.test(el.amount.value[i])) sig++;
    S.v.amount = sanitizeAmount(el.amount.value);
    var formatted = formatAmount(S.v.amount, S.v.currency);
    el.amount.value = formatted;
    var pos = 0, seen = 0;
    while (pos < formatted.length && seen < sig) { if (/[0-9.]/.test(formatted[pos])) seen++; pos++; }
    try { el.amount.setSelectionRange(pos, pos); } catch (e) { /* not supported */ }
    userEdit('amount');
    el.amountMsg.hidden = true;
  });
  el.currency.addEventListener('change', function () {
    S.v.currency = el.currency.value;
    el.amount.value = formatAmount(S.v.amount, S.v.currency);
    userEdit('currency');
  });
  el.description.addEventListener('input', function () { S.v.description = el.description.value; userEdit('description'); });
  el.account.addEventListener('change', function () { S.v.accountId = el.account.value ? +el.account.value : ''; userEdit('account'); });
  el.payGroup.addEventListener('change', function (e) {
    if (e.target.name !== 'tmr-pay') return;
    S.v.payment = e.target.value; userEdit('payment'); el.payMsg.hidden = true; syncActive();
  });
  el.dateGroup.addEventListener('change', function (e) {
    if (e.target.name !== 'tmr-date') return;
    if (e.target.value === 'custom') {
      // The radio only becomes the choice once a date is picked.
      paintDate(); openPicker(); return;
    }
    S.v.date = e.target.value === 'today' ? todayISO() : addDays(todayISO(), -1);
    paintDate(); userEdit('date');
  });
  function openPicker() {
    try { if (el.dateInput.showPicker) { el.dateInput.showPicker(); return; } } catch (e) { /* needs a gesture */ }
    el.dateInput.focus();
  }
  el.dateInput.addEventListener('click', openPicker);
  el.dateInput.addEventListener('change', function () {
    if (!validISO(el.dateInput.value)) return;
    S.v.date = el.dateInput.value;
    paintDate(); userEdit('date');
  });
  el.dateCustom.addEventListener('keydown', function (e) {
    if (e.key === ' ' && dateKind(S.v.date) !== 'custom') { e.preventDefault(); openPicker(); }
  });

  // Category select, including "+ Create category"
  el.category.addEventListener('change', function () {
    if (el.category.value === '__create__') {
      // The dropdown component writes its label after this handler, so reset the value just after it.
      setTimeout(function () { el.category.value = S.v.category || ''; refreshSelect(el.category); }, 0);
      el.catMsg.hidden = true; el.catName.value = '';
      show(el.catInline, true); el.catName.focus();
      return;
    }
    S.v.category = el.category.value; userEdit('category'); el.categoryMsg.hidden = true;
  });
  function closeCatInline() { show(el.catInline, false); ctl(el.category).focus(); }
  el.catCancel.addEventListener('click', closeCatInline);
  el.catName.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); el.catSave.click(); }
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeCatInline(); }
  });
  el.catSave.addEventListener('click', function () {
    var name = el.catName.value.trim();
    if (!name) { el.catMsg.textContent = t('categoryEmpty'); el.catMsg.hidden = false; el.catName.focus(); return; }
    el.catSave.disabled = true;
    fetch(URLS.category, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify({ name: name })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (!res.j.success) throw new Error(res.j.error || t('categoryFailed'));
        if (S.data && S.data.categories.indexOf(res.j.name) < 0) {
          S.data.categories.push(res.j.name);
          S.data.categories.sort(function (a, b) { return a.toLowerCase() < b.toLowerCase() ? -1 : 1; });
        }
        fillCategories();
        S.v.category = res.j.name; userEdit('category'); paint();
        show(el.catInline, false);
        ctl(el.category).focus();
      })
      .catch(function (err) { el.catMsg.textContent = err.message || t('categoryFailed'); el.catMsg.hidden = false; })
      .then(function () { el.catSave.disabled = false; });
  });

  // ── Validation & save ─────────────────────────────────────────────────────
  function fieldMsg(f, node, text) {
    fieldEl[f].classList.add('is-invalid');
    node.textContent = text; node.hidden = false;
  }
  function validate() {
    var first = null;
    Object.keys(fieldEl).forEach(function (f) { fieldEl[f].classList.remove('is-invalid'); });
    [el.amountMsg, el.categoryMsg, el.payMsg].forEach(function (n) { n.hidden = true; });
    if (!(amountNumber() > 0)) { fieldMsg('amount', el.amountMsg, t('errAmount')); first = first || el.amount; }
    if (!validISO(S.v.date)) { fieldMsg('date', el.dateMsg, t('errDate')); first = first || el.dateGroup.querySelector('input'); }
    if (!S.v.category) { fieldMsg('category', el.categoryMsg, t('errCategory')); first = first || ctl(el.category); }
    if (!S.v.payment) { fieldMsg('payment', el.payMsg, t('errPayment')); first = first || el.payGroup.querySelector('input'); }
    return first;
  }
  function setBusy(on) {
    S.saving = on;
    [el.add, el.addAnother, el.cancel].forEach(function (b) { b.disabled = on; });
    if (on) el.add.textContent = t('adding'); else el.add.innerHTML = addHtml;
  }

  function attemptAdd(another) {
    if (S.saving) return;
    if (S.view === 'A') {
      if (el.quick.value.trim()) parse(); else enterManual();
      return;
    }
    var bad = validate();
    if (bad) { bad.focus(); return; }
    var limit = S.data ? (S.data.large_amount_thresholds[S.v.currency] || 5000) : 100000;
    if (!S.largeOk && amountNumber() >= limit) { askLarge(another); return; }
    save(another);
  }

  function askLarge(another) {
    el.largeText.textContent = t('largeAmount', { amount: symbolFmt(amountNumber(), S.v.currency) });
    el.largeYes.textContent = t('yesAdd'); el.largeEdit.textContent = t('editAmount');
    el.largeCapital.textContent = t('asCapital');
    el.largeCapital.href = URLS.capital + '?amount=' + encodeURIComponent(S.v.amount);
    el.largeYes.onclick = function () { S.largeOk = true; show(el.cLarge, false); save(another); };
    el.largeEdit.onclick = function () { show(el.cLarge, false); el.amount.focus(); el.amount.select(); };
    show(el.cLarge, true); el.largeYes.focus();
  }

  function showError(text, actions) {
    el.bErrorText.textContent = text;
    el.bErrorActions.textContent = '';
    (actions || []).forEach(function (a) {
      var n = document.createElement(a.href ? 'a' : 'button');
      n.className = a.href ? 'tmr-linkbtn tmr-linkbtn--small' : 'btn-action-outline tmr-btn';
      n.textContent = a.label;
      if (a.href) n.href = a.href; else { n.type = 'button'; n.addEventListener('click', a.onClick); }
      el.bErrorActions.appendChild(n);
    });
    show(el.bError, true);
    live(text);
  }

  function save(another) {
    if (S.saving) return;
    S.lastAnother = another;
    show(el.bError, false);
    setBusy(true);
    var body = {
      key: S.key, amount: S.v.amount, currency: S.v.currency, date: S.v.date,
      description: S.v.description.trim(), category: S.v.category,
      account_id: S.v.accountId, payment_method: S.v.payment,
      meta: {
        mode: S.usedParse ? 'quick' : 'manual', edited: Object.keys(S.edited),
        add_another: !!another, duration_ms: Date.now() - S.openedAt
      }
    };
    fetch(URLS.save, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json().then(function (j) { return { status: r.status, j: j }; }); })
      .then(function (res) {
        setBusy(false);
        if (res.j && res.j.success) return onSaved(res.j, another);
        var j = res.j || {};
        if (j.code === 'limit') {
          showError(j.error, [{ label: t('upgrade'), href: j.upgrade_url || URLS.pricing }]);
        } else {
          var fe = j.field_errors || {};
          if (fe.amount) fieldMsg('amount', el.amountMsg, t('errAmount'));
          if (fe.category) fieldMsg('category', el.categoryMsg, t('errCategory'));
          if (fe.date) fieldMsg('date', el.dateMsg, t('errDate'));
          showError(j.error || t('saveFailed'), []);
        }
      })
      .catch(function () {
        setBusy(false);
        showError(t('saveFailed'), [{ label: t('retry'), onClick: function () { save(S.lastAnother); } }]);
      });
  }

  function onSaved(j, another) {
    if (S.data && j.account_id && j.account_label) {
      S.data.accounts.forEach(function (a) { if (a.id === j.account_id) a.label = j.account_label; });
      populateSelects();
      S.data.default_account_id = j.account_id; S.data.default_account_source = 'last_used';
      S.data.default_payment_method = S.v.payment || S.data.default_payment_method;
      S.data.usuals = [];  // refreshed on the next page load
    }
    var parts = [j.expense.amount_display, j.expense.category];
    if (j.expense.account) parts.push(j.expense.account);
    var toast = { summary: parts.join(' · '), undoUrl: j.undo_url, editUrl: j.edit_url };
    if (another) {
      S.stickyDate = S.v.date !== todayISO() ? S.v.date : null;
      resetCard(true);
      showToast(toast, TOAST_MS);
      el.quick.focus();
    } else {
      finish(toast);
    }
  }

  // Done: remember the toast for the next page and go back to where the user came from.
  function finish(toast) {
    try { sessionStorage.setItem(TOAST_KEY, JSON.stringify({ toast: toast, until: Date.now() + TOAST_MS })); } catch (e) { /* private mode */ }
    leave();
  }

  // ── Page lifecycle ────────────────────────────────────────────────────────
  // New Expense is an ordinary page: Cancel / Close / a finished add go back to where the user came from.
  function leave() { window.location.assign(S.nextUrl); }


  el.cancel.addEventListener('click', function () { if (!S.saving) leave(); });
  el.go.addEventListener('click', parse);
  el.manual.addEventListener('click', enterManual);
  el.add.addEventListener('click', function () { attemptAdd(false); });
  el.addAnother.addEventListener('click', function () { attemptAdd(true); });
  el.dateNoteReset.addEventListener('click', function () {
    S.stickyDate = null; applyDefaults(true); paint(); el.quick.focus();
  });

  // ── Keyboard ──────────────────────────────────────────────────────────────
  function focusTargets() {
    return [
      el.amount,
      el.dateGroup.querySelector('input:checked') || el.dateGroup.querySelector('input'),
      el.description, ctl(el.category), ctl(el.account),
      el.payGroup.querySelector('input:checked') || el.payGroup.querySelector('input')
    ];
  }
  function seqIndex(target) {
    if (target === el.amount) return 0;
    if (el.dateGroup.contains(target) && target.type === 'radio') return 1;
    if (target === el.description) return 2;
    if (target === ctl(el.category)) return 3;
    if (target === ctl(el.account)) return 4;
    if (el.payGroup.contains(target) && target.type === 'radio') return 5;
    return -1;
  }
  el.quick.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.isComposing && !e.ctrlKey && !e.metaKey) { e.preventDefault(); parse(); }
  });
  el.stateB.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' || e.isComposing || e.ctrlKey || e.metaKey || e.shiftKey) return;
    var i = seqIndex(e.target);
    if (i < 0) return;
    e.preventDefault();
    if (i === FIELDS.length - 1) attemptAdd(false);
    else focusTargets()[i + 1].focus();
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); attemptAdd(false); return; }
    if (e.key === 'Escape') {
      if (!el.cLarge.hidden) { show(el.cLarge, false); return; }
    }
  });

  // ── Voice ─────────────────────────────────────────────────────────────────
  var rec = null;
  // Looked up at tap time (as the old form did): iOS web apps don't always expose it at page load.
  function speechCtor() { return window.SpeechRecognition || window.webkitSpeechRecognition; }

  function stopListening() {
    el.mic.classList.remove('is-listening'); el.mic.setAttribute('aria-pressed', 'false');
    if (rec) { try { rec.onresult = rec.onerror = rec.onend = null; rec.abort(); } catch (e) { /* noop */ } rec = null; }
  }
  el.mic.addEventListener('click', function () {
    var SR = speechCtor();
    if (!SR) {
      // No Web Speech API in this view: the keyboard's own dictation still works.
      el.voiceMsg.textContent = t('micUnsupported'); show(el.voiceMsg, true); el.quick.focus();
      return;
    }
    if (rec) { stopListening(); return; }
    show(el.voiceMsg, false);
    try {
      rec = new SR();
      rec.lang = { hi: 'hi-IN', mr: 'mr-IN' }[LANG] || 'en-IN';
      rec.continuous = false; rec.interimResults = false;
      rec.onresult = function (ev) {
        var text = ev.results && ev.results[0] && ev.results[0][0] ? ev.results[0][0].transcript : '';
        stopListening();
        if (!text) return;
        el.quick.value = text;
        sendEvent('voice_used', { lang: LANG });
        parse();
      };
      rec.onerror = function (ev) {
        stopListening();
        if (ev.error === 'not-allowed' || ev.error === 'service-not-allowed') {
          el.voiceMsg.textContent = t('micBlocked'); show(el.voiceMsg, true);
        }
      };
      rec.onend = stopListening;
      el.mic.classList.add('is-listening'); el.mic.setAttribute('aria-pressed', 'true');
      live(t('listening'));
      rec.start();                          // must stay inside the click handler (user gesture)
    } catch (e) { stopListening(); }
  });

  // ── Analytics (allow-listed on the server; never amounts, text or names) ─
  function sendEvent(name, props) {
    try {
      fetch(URLS.event, {
        method: 'POST', credentials: 'same-origin', keepalive: true,
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
        body: JSON.stringify({ event: name, props: props || {} })
      }).catch(function () { /* analytics must never break entry */ });
    } catch (e) { /* noop */ }
  }

  // ── Start ─────────────────────────────────────────────────────────────────
  S.nextUrl = root.dataset.next || '/expenses/';
  hooks.nextUrl = function () { return S.nextUrl; };
  hooks.afterUndo = function (j) {
    if (S.data && j.account_id && j.account_label) {
      S.data.accounts.forEach(function (a) { if (a.id === j.account_id) a.label = j.account_label; });
      populateSelects(); paint();
    }
  };
  S.entry = { pwa: 'pwa_shortcut' }[root.dataset.entry] || 'deep_link';
  S.openedAt = Date.now();
  resetCard(false);
  el.quick.focus();
  ensureData().catch(function () { showLoadError(); });
  sendEvent('expense_form_opened', { entry: S.entry });
})();
