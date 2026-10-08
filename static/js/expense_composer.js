/* New Expense composer: type one line, review six fields, add.
 * Markup: templates/components/expense_composer.html   Styles: static/css/expense_composer.css
 * Endpoints: parse-expense (dry run), expense-composer-{data,save,event}, expense-composer-undo.
 */
(function () {
  'use strict';

  var root = document.getElementById('tmr-composer');
  var meta = document.getElementById('tmr-c-i18n');
  if (!root || !meta) return;

  var $ = function (id) { return document.getElementById(id); };
  var I = {};
  meta.querySelectorAll('[data-k]').forEach(function (el) { I[el.dataset.k] = el.textContent; });
  function t(key, vars) {
    var s = I[key] != null ? I[key] : key;
    if (vars) Object.keys(vars).forEach(function (k) { s = s.split('{' + k + '}').join(vars[k]); });
    return s;
  }

  var URLS = {
    parse: meta.dataset.parseUrl,
    data: meta.dataset.dataUrl,
    save: meta.dataset.saveUrl,
    event: meta.dataset.eventUrl,
    category: meta.dataset.categoryUrl,
    capital: meta.dataset.capitalUrl,
    addPath: meta.dataset.addPath,
    pricing: meta.dataset.pricingUrl
  };
  var CSRF = meta.dataset.csrf;
  var LANG = (document.documentElement.lang || 'en').slice(0, 2).toLowerCase();
  var LOCALE = { hi: 'hi-IN', mr: 'mr-IN' }[LANG] || 'en-IN';
  var TOAST_KEY = 'tmr_composer_toast';
  var TOAST_MS = 8000;

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
    cDiscard: $('tmr-confirm-discard'), discardText: $('tmr-discard-text'),
    discardKeep: $('tmr-discard-keep'), discardGo: $('tmr-discard-go'),
    cLarge: $('tmr-confirm-large'), largeText: $('tmr-large-text'), largeYes: $('tmr-large-yes'),
    largeEdit: $('tmr-large-edit'), largeCapital: $('tmr-large-capital'),
    live: $('tmr-live'), toasts: $('tmr-toasts'), card: root.querySelector('.tmr-c__card')
  };
  var fieldEl = {};
  root.querySelectorAll('[data-field]').forEach(function (n) { fieldEl[n.dataset.field] = n; });
  var addLabel = el.add.textContent;
  var pickLabel = el.pickFace.textContent;

  // ── State ─────────────────────────────────────────────────────────────────
  var S = {
    open: false, view: 'A', data: null, loading: null,
    v: { amount: '', currency: '₹', date: '', description: '', category: '', accountId: '', payment: 'Cash' },
    origin: {}, tag: {}, check: {}, edited: {}, usedParse: false,
    key: '', openedAt: 0, entry: 'other', stickyDate: null, saving: false, dirtyPage: false,
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

  function paintDate() {
    var iso = S.v.date, kind = dateKind(iso);
    el.dateGroup.querySelectorAll('input[type=radio]').forEach(function (r) { r.checked = r.value === kind; });
    el.pickFace.textContent = kind === 'custom' ? fmtDate(iso) : pickLabel;
    el.dateInput.value = iso;
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
    el.payGroup.querySelectorAll('input').forEach(function (r) { r.checked = r.value === S.v.payment; });
    paintDate();
    FIELDS.forEach(paintTag);
    updateDateNote();
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
  }
  function fillCategories() {
    el.category.textContent = '';
    el.category.add(new Option(t('selectCategory'), ''));
    (S.data ? S.data.categories : []).forEach(function (c) { el.category.add(new Option(c, c)); });
    el.category.add(new Option(t('createCategory'), '__create__'));
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
    show(el.bNotice, false); show(el.bError, false); show(el.cDiscard, false); show(el.cLarge, false);
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
      b.type = 'button'; b.className = 'tmr-suggest'; b.textContent = c.label;
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
    S.v.payment = e.target.value; userEdit('payment'); el.payMsg.hidden = true;
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
      el.category.value = S.v.category || '';
      el.catMsg.hidden = true; el.catName.value = '';
      show(el.catInline, true); el.catName.focus();
      return;
    }
    S.v.category = el.category.value; userEdit('category'); el.categoryMsg.hidden = true;
  });
  function closeCatInline() { show(el.catInline, false); el.category.focus(); }
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
        el.category.focus();
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
    if (!S.v.category) { fieldMsg('category', el.categoryMsg, t('errCategory')); first = first || el.category; }
    if (!S.v.payment) { fieldMsg('payment', el.payMsg, t('errPayment')); first = first || el.payGroup.querySelector('input'); }
    return first;
  }
  function setBusy(on) {
    S.saving = on;
    [el.add, el.addAnother, el.cancel].forEach(function (b) { b.disabled = on; });
    el.add.textContent = on ? t('adding') : addLabel;
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
    S.dirtyPage = true;
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

  // Leave the card and make the page behind it show the new expense.
  function finish(toast) {
    try { sessionStorage.setItem(TOAST_KEY, JSON.stringify({ toast: toast, until: Date.now() + TOAST_MS })); } catch (e) { /* private mode */ }
    teardown();
    if (S.deepLink) { window.location.assign(S.nextUrl); return; }
    leaveHistory(function () { window.location.reload(); });
  }

  function leaveHistory(done) {
    if (!S.pushed) { done(); return; }
    S.pushed = false;
    var called = false;
    function once() { if (called) return; called = true; window.removeEventListener('popstate', once); done(); }
    window.addEventListener('popstate', once);
    history.back();
    setTimeout(once, 400);
  }

  // ── Toasts ────────────────────────────────────────────────────────────────
  function showToast(opts, ms) {
    var box = document.createElement('div');
    box.className = 'tmr-toast';
    var msg = document.createElement('span');
    msg.className = 'tmr-toast__msg';
    msg.textContent = opts.plain || t('addedToast', { summary: opts.summary });
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
      var next = S.deepLink ? S.nextUrl : (location.pathname + location.search);
      e.href = opts.editUrl + (opts.editUrl.indexOf('?') < 0 ? '?' : '&') + 'next=' + encodeURIComponent(next);
      box.appendChild(e);
    }
    box.addEventListener('mouseenter', function () { clearTimeout(timer); });
    box.addEventListener('mouseleave', function () { arm(2500); });
    box.addEventListener('focusin', function () { clearTimeout(timer); });
    box.addEventListener('focusout', function () { arm(2500); });
    el.toasts.appendChild(box);
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
        if (S.data && res.j.account_id && res.j.account_label) {
          S.data.accounts.forEach(function (a) { if (a.id === res.j.account_id) a.label = res.j.account_label; });
          populateSelects(); paint();
        }
        showToast({ plain: t('undone') }, 3000);
        if (S.open) S.dirtyPage = true; else setTimeout(function () { window.location.reload(); }, 600);
      })
      .catch(function () { if (btn) btn.disabled = false; showToast({ plain: t('undoFailed') }, 5000); });
  }

  // Toast that survives the post-add page refresh.
  try {
    var saved = JSON.parse(sessionStorage.getItem(TOAST_KEY) || 'null');
    sessionStorage.removeItem(TOAST_KEY);
    if (saved && saved.until - Date.now() > 800) showToast(saved.toast, saved.until - Date.now());
  } catch (e) { /* ignore */ }

  // ── Open / close ──────────────────────────────────────────────────────────
  function syncViewport() {
    var vv = window.visualViewport;
    if (!vv) return;
    root.style.setProperty('--tmr-vv-h', vv.height + 'px');
    root.style.setProperty('--tmr-vv-top', vv.offsetTop + 'px');
  }

  function open(entry, opts) {
    opts = opts || {};
    if (S.open) { el.quick.focus(); return; }
    S.open = true; S.entry = entry || 'other'; S.openedAt = Date.now();
    S.opener = opts.opener || document.activeElement;
    root.hidden = false;
    document.documentElement.classList.add('tmr-c-open');
    resetCard(false);
    syncViewport();
    if (window.visualViewport) {
      visualViewport.addEventListener('resize', syncViewport);
      visualViewport.addEventListener('scroll', syncViewport);
    }
    el.quick.focus();                       // synchronous so iOS shows the keyboard
    if (!opts.noHistory) { history.pushState({ tmrComposer: 1 }, ''); S.pushed = true; }
    ensureData().catch(function () { showLoadError(); });
    sendEvent('expense_form_opened', { entry: S.entry });
    if (opts.text) { el.quick.value = opts.text; parse(); }
  }

  function teardown() {
    S.open = false;
    S.stickyDate = null;
    root.hidden = true;
    document.documentElement.classList.remove('tmr-c-open');
    if (window.visualViewport) {
      visualViewport.removeEventListener('resize', syncViewport);
      visualViewport.removeEventListener('scroll', syncViewport);
    }
    stopListening();
  }

  function close() {
    teardown();
    var back = function () {
      if (S.deepLink) { window.location.assign(S.nextUrl); return; }
      if (S.dirtyPage) { window.location.reload(); return; }
      if (S.opener && S.opener.focus && document.contains(S.opener)) S.opener.focus();
    };
    leaveHistory(back);
  }

  function hasUnsaved() {
    return !!(el.quick.value.trim() || S.v.amount || S.v.description || Object.keys(S.edited).length);
  }
  function requestClose() {
    if (S.saving) return;
    if (!hasUnsaved()) { close(); return; }
    el.discardText.textContent = t('discardAsk');
    el.discardKeep.textContent = t('keepEditing'); el.discardGo.textContent = t('discard');
    el.discardKeep.onclick = function () { show(el.cDiscard, false); (S.view === 'A' ? el.quick : el.amount).focus(); };
    el.discardGo.onclick = function () { show(el.cDiscard, false); close(); };
    show(el.cDiscard, true); el.discardKeep.focus();
  }

  root.addEventListener('click', function (e) {
    if (e.target.closest('[data-composer-close]')) requestClose();
  });
  el.cancel.addEventListener('click', function () { if (!S.saving) close(); });
  el.go.addEventListener('click', parse);
  el.manual.addEventListener('click', enterManual);
  el.add.addEventListener('click', function () { attemptAdd(false); });
  el.addAnother.addEventListener('click', function () { attemptAdd(true); });
  el.dateNoteReset.addEventListener('click', function () {
    S.stickyDate = null; applyDefaults(true); paint(); el.quick.focus();
  });

  window.addEventListener('popstate', function () {
    if (!S.open || !S.pushed) return;
    // Browser/phone Back: keep the card if there is something to lose.
    if (hasUnsaved()) { history.pushState({ tmrComposer: 1 }, ''); requestClose(); return; }
    S.pushed = false; teardown();
    if (S.dirtyPage) window.location.reload();
  });

  // ── Keyboard ──────────────────────────────────────────────────────────────
  function focusTargets() {
    return [
      el.amount,
      el.dateGroup.querySelector('input:checked') || el.dateGroup.querySelector('input'),
      el.description, el.category, el.account,
      el.payGroup.querySelector('input:checked') || el.payGroup.querySelector('input')
    ];
  }
  function seqIndex(target) {
    if (target === el.amount) return 0;
    if (el.dateGroup.contains(target) && target.type === 'radio') return 1;
    if (target === el.description) return 2;
    if (target === el.category) return 3;
    if (target === el.account) return 4;
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

  root.addEventListener('keydown', function (e) {
    if (!S.open) return;
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); attemptAdd(false); return; }
    if (e.key === 'Escape') {
      e.preventDefault();
      if (!el.cDiscard.hidden) { show(el.cDiscard, false); return; }
      if (!el.cLarge.hidden) { show(el.cLarge, false); return; }
      requestClose();
      return;
    }
    if (e.key === 'Tab') {                  // keep focus inside the dialog
      var items = Array.prototype.filter.call(
        el.card.querySelectorAll('a[href], button, input, select, textarea, [tabindex]'),
        function (n) {
          return !n.disabled && n.tabIndex >= 0 && n.getClientRects().length > 0 &&
            !(n.type === 'radio' && !n.checked && n.name && el.card.querySelector('input[name="' + n.name + '"]:checked'));
        });
      if (!items.length) return;
      var first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });

  // Modal behaviour: if something outside the card grabs focus (e.g. Bootstrap returning it to a
  // trigger after its offcanvas closes), hand it back to the last control used inside the card.
  var lastInside = null;
  root.addEventListener('focusin', function (e) { lastInside = e.target; });
  document.addEventListener('focusin', function (e) {
    if (!S.open || root.contains(e.target)) return;
    (lastInside && document.contains(lastInside) ? lastInside : el.quick).focus();
  });

  // Global "N" shortcut (desktop), never while typing or when another dialog is up.
  document.addEventListener('keydown', function (e) {
    if (S.open || e.defaultPrevented) return;
    if (e.key !== 'n' && e.key !== 'N') return;
    if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
    var a = document.activeElement, tag = a && a.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (a && a.isContentEditable)) return;
    if (document.querySelector('.modal.show, .offcanvas.show')) return;
    e.preventDefault();
    open('shortcut_key');
  });

  // ── Voice ─────────────────────────────────────────────────────────────────
  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  var rec = null;
  if (SR && window.isSecureContext !== false) el.mic.hidden = false;

  function stopListening() {
    el.mic.classList.remove('is-listening'); el.mic.setAttribute('aria-pressed', 'false');
    if (rec) { try { rec.onresult = rec.onerror = rec.onend = null; rec.abort(); } catch (e) { /* noop */ } rec = null; }
  }
  el.mic.addEventListener('click', function () {
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

  // ── Entry points ──────────────────────────────────────────────────────────
  function guessEntry(a) {
    if (a.dataset.composerEntry) return a.dataset.composerEntry;
    if (a.closest('#addActionsSheet')) return 'mobile_sheet';
    if (a.closest('.sidebar, #sidebar, [class*="sidebar"]')) return 'sidebar';
    if (a.closest('nav, header')) return 'navbar';
    return 'empty_state';
  }
  document.addEventListener('click', function (e) {
    if (e.defaultPrevented) return;
    var trigger = e.target.closest('[data-open-composer]');
    var a = trigger ? null : e.target.closest('a[href]');
    if (trigger) {
      e.preventDefault();
      open(trigger.dataset.openComposer || 'other', { opener: trigger });
      return;
    }
    if (!a || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    if (a.target && a.target !== '_self') return;
    var url;
    try { url = new URL(a.href, window.location.href); } catch (err) { return; }
    if (url.origin !== window.location.origin || url.pathname !== URLS.addPath) return;
    e.preventDefault();
    var sheet = a.closest('.offcanvas.show');
    if (sheet && window.bootstrap) { var inst = bootstrap.Offcanvas.getInstance(sheet); if (inst) inst.hide(); }
    open(guessEntry(a), { opener: a });
  });

  // Dashboard quick-add bar: type there, Enter opens the composer already parsed.
  document.addEventListener('keydown', function (e) {
    var input = e.target.closest && e.target.closest('[data-composer-bar-input]');
    if (!input || e.key !== 'Enter' || e.isComposing) return;
    e.preventDefault();
    var text = input.value.trim(); input.value = '';
    open('dashboard_bar', { opener: input, text: text });
  });
  document.addEventListener('click', function (e) {
    var go = e.target.closest('[data-composer-bar-go]');
    if (!go) return;
    var input = document.querySelector('[data-composer-bar-input]');
    var text = input ? input.value.trim() : '';
    if (input) input.value = '';
    open('dashboard_bar', { opener: go, text: text });
  });

  // Deep link (/expenses/add/, PWA shortcut, old bookmarks): open straight away.
  var auto = document.querySelector('[data-composer-autoopen]');
  if (auto) {
    S.deepLink = true;
    S.nextUrl = auto.dataset.next || '/expenses/';
    open(auto.dataset.entry === 'pwa' ? 'pwa_shortcut' : 'deep_link', { noHistory: true });
  }

  window.TMRComposer = { open: open };
})();
