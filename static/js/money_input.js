/* Currency-aware amount inputs.

   Any <input data-money> shows its number grouped the way the selected currency is written while the person
   types: 100000 becomes 1,00,000 for the rupee and 100,000 for every other currency. The first money input of a
   form that carries a currency select (data-money-primary) is joined with that select into one control:
   [₹ ▾][ 1,00,000 ].

   The rest of the page does not have to know. The input keeps working like a number field:
     - input.value gives the plain number ("100000.50"), so existing scripts that parseFloat() it, or set it,
       keep working (setting formats it for display);
     - the form is submitted with the plain number (formdata event), also when htmx or fetch builds the request.
   The server also tolerates commas (forms.MoneyInput), so a missing script never loses an amount.

   Opt in with data-money. Optional: data-money-primary, data-money-currency="#selector" (which select),
   data-money-decimals="2". Negative amounts are allowed unless the input has min >= 0. */
(function () {
  'use strict';

  var valueProp = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
  var selectValueProp = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
  var MAX_INT_DIGITS = 15;

  function profileCurrency() { return window.TMR_CURRENCY || '₹'; }

  // ── Number handling ──────────────────────────────────────────────────────
  function decimalsFor(el) {
    var d = el.getAttribute('data-money-decimals');
    if (d !== null && d !== '' && !isNaN(parseInt(d, 10))) return parseInt(d, 10);
    var step = el.getAttribute('data-money-step') || '0.01';
    if (step === 'any' || step.indexOf('.') < 0) return step === 'any' ? 2 : 0;
    return Math.min(6, step.split('.')[1].length);
  }

  function allowsNegative(el) {
    var min = el.getAttribute('data-money-min');
    return min === null || min === '' || parseFloat(min) < 0;
  }

  /* Keep digits and one decimal point (and one leading minus where allowed); cap the decimals. */
  function sanitize(str, decimals, negative) {
    str = String(str == null ? '' : str);
    var neg = negative && /^\s*-/.test(str);
    var out = '', dot = false, dec = 0;
    for (var i = 0; i < str.length; i++) {
      var ch = str.charAt(i);
      if (ch >= '0' && ch <= '9') {
        if (dot) { if (dec >= decimals) continue; dec++; }
        out += ch;
      } else if (ch === '.' && !dot && decimals > 0) {
        dot = true; out += '.';
      }
    }
    var parts = out.split('.');
    parts[0] = parts[0].replace(/^0+(?=\d)/, '').slice(0, MAX_INT_DIGITS);
    if (out.charAt(0) === '.') parts[0] = '0';
    out = parts.length > 1 ? parts[0] + '.' + parts[1] : parts[0];
    return (neg ? '-' : '') + out;
  }

  /* The rupee groups the last three digits, then pairs (1,00,000); every other currency groups in threes. */
  function groupInt(digits, cur) {
    if (digits.length <= 3) return digits;
    if (cur === '₹') {
      return digits.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ',') + ',' + digits.slice(-3);
    }
    return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  function format(raw, cur) {
    if (!raw || raw === '-') return raw || '';
    var sign = raw.charAt(0) === '-' ? '-' : '';
    var body = sign ? raw.slice(1) : raw;
    var p = body.split('.');
    return sign + groupInt(p[0], cur) + (p.length > 1 ? '.' + p[1] : '');
  }

  // ── The currency an input is written in ──────────────────────────────────
  function currencySelect(el) {
    if (el._moneySelect !== undefined) return el._moneySelect;
    var sel = null, ref = el.getAttribute('data-money-currency');
    if (ref) sel = document.querySelector(ref);
    if (!sel && el.form) sel = el.form.querySelector('select[name="currency"]');
    el._moneySelect = sel;
    return sel;
  }

  function currencyOf(el) {
    var sel = currencySelect(el);
    return (sel && selectValueProp.get.call(sel)) || profileCurrency();
  }

  // ── Display ──────────────────────────────────────────────────────────────
  function rawOf(el) {
    return sanitize(valueProp.get.call(el), decimalsFor(el), allowsNegative(el));
  }

  function paint(el, raw, keepCaret) {
    var shown = format(raw, currencyOf(el));
    if (valueProp.get.call(el) === shown) return;
    var sig = 0;
    if (keepCaret && document.activeElement === el) {
      var caret = el.selectionStart || 0, text = valueProp.get.call(el);
      for (var i = 0; i < caret; i++) if (/[0-9.]/.test(text.charAt(i))) sig++;
    }
    valueProp.set.call(el, shown);
    if (keepCaret && document.activeElement === el) {
      var pos = 0, seen = 0;
      while (pos < shown.length && seen < sig) { if (/[0-9.]/.test(shown.charAt(pos))) seen++; pos++; }
      try { el.setSelectionRange(pos, pos); } catch (e) { /* not supported */ }
    }
  }

  function repaintAll(form) {
    var inputs = (form || document).querySelectorAll('input[data-money-ready]');
    for (var i = 0; i < inputs.length; i++) paint(inputs[i], rawOf(inputs[i]), false);
  }

  // ── Combined [currency ▾][ amount ] control ──────────────────────────────
  function symbolOf(select) {
    var opt = select.options[select.selectedIndex];
    if (!opt) return '';
    var m = /\(([^)]+)\)\s*$/.exec(opt.textContent);
    return m ? m[1] : (opt.value || opt.textContent).trim();
  }

  function isVisible(node) { return node.style.display !== 'none' && !node.classList.contains('d-none'); }

  function colSpan(node) {
    var m = /(?:^|\s)col(?:-([a-z]+))?-(\d+)(?=\s|$)/.exec(node.className);
    return m ? { bp: m[1] || '', n: parseInt(m[2], 10) } : null;
  }

  function setColSpan(node, bp, n) {
    node.className = node.className.replace(/(?:^|\s)col(?:-[a-z]+)?(?:-\d+)?(?=\s|$)/g, ' ').trim() +
      ' col' + (bp ? '-' + bp : '') + '-' + n;
  }

  function tidyOldSlot(slot, select, group, amountEl) {
    // `slot` is the wrapper the select used to live in. Its label and notes follow the control, and when it
    // sat beside the amount in the same row, the amount takes over its width.
    if (!slot || slot === group || slot.contains(group)) return;
    var controls = slot.querySelectorAll('select, input:not([type=hidden]), textarea');
    var notes = slot.querySelectorAll('.text-danger, .form-text, .invalid-feedback');
    for (var i = 0; i < notes.length; i++) group.parentNode.insertBefore(notes[i], group.nextSibling);
    var labels = slot.querySelectorAll('label');
    for (var j = 0; j < labels.length; j++) labels[j].style.setProperty('display', 'none', 'important');
    if (controls.length !== 0) return;
    slot.style.setProperty('display', 'none', 'important');
    var row = slot.parentElement;
    if (!row || !/(^|\s)row(\s|$)/.test(row.className)) return;
    var mine = amountEl;
    while (mine && mine.parentElement !== row) mine = mine.parentElement;
    var a = mine && mine !== slot && colSpan(mine), b = colSpan(slot);
    if (a && b && a.bp === b.bp) { setColSpan(mine, a.bp, Math.min(12, a.n + b.n)); return; }
    var rest = Array.prototype.filter.call(row.children, function (c) { return c !== slot && isVisible(c); });
    if (rest.length === 1 && colSpan(rest[0])) setColSpan(rest[0], '', 12);
  }

  function slotOf(select) {
    var p = select.parentElement;
    if (p && p.classList.contains('tmr-money-cur')) return null;
    return p;
  }

  function combine(el) {
    var select = currencySelect(el);
    if (!select || select._moneyCombined || el.hasAttribute('data-money-single')) return;
    if (!el.hasAttribute('data-money-primary')) return;
    var form = el.form;
    if (form && form.querySelector('input[data-money-primary][data-money-grouped]')) return;
    select._moneyCombined = true;
    el.setAttribute('data-money-grouped', '');

    // Every select is turned into a searchable dropdown at load (app_base.js). The currency select is a short list
    // shown as just its symbol, so it stays a plain select: undo that if it already happened, and mark it so the
    // dropdown script leaves it alone otherwise.
    select.setAttribute('data-searchable-init', '1');
    var widget = select.closest('.searchable-select-wrapper');
    if (widget) widget.parentNode.replaceChild(select, widget);
    select.style.display = '';

    var oldSlot = slotOf(select);
    var label = oldSlot && oldSlot.querySelector('label[for="' + select.id + '"]');
    if (!select.getAttribute('aria-label') && label) select.setAttribute('aria-label', label.textContent.trim());

    var group = el.parentElement;
    var large = el.classList.contains('form-control-lg');
    if (!group || !group.classList.contains('input-group')) {
      group = document.createElement('div');
      el.parentNode.insertBefore(group, el);
      group.appendChild(el);
      group.className = 'input-group';
    } else {
      var prefix = group.querySelector('.input-group-text');       // a fixed currency sign: the select replaces it
      if (prefix && prefix.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) prefix.remove();
      el.classList.remove('border-start-0', 'ps-0');
    }
    group.classList.add('tmr-money-group');
    if (large) group.classList.add('input-group-lg');

    var holder = document.createElement('div');
    holder.className = 'tmr-money-cur';
    var sym = document.createElement('span');
    sym.className = 'tmr-money-sym';
    sym.setAttribute('aria-hidden', 'true');
    holder.appendChild(select);
    holder.appendChild(sym);
    group.insertBefore(holder, group.firstChild);

    var paintSymbol = function () {
      sym.textContent = symbolOf(select);
      holder.classList.toggle('is-disabled', select.disabled);
    };
    // Scripts that choose a currency by assigning select.value (a prefill, a reset) change the symbol too.
    Object.defineProperty(select, 'value', {
      configurable: true,
      get: function () { return selectValueProp.get.call(select); },
      set: function (v) { selectValueProp.set.call(select, v); paintSymbol(); repaintAll(form); }
    });
    select.addEventListener('change', function () { paintSymbol(); repaintAll(form); });
    paintSymbol();
    tidyOldSlot(oldSlot, select, group, el);
  }

  // ── One input ────────────────────────────────────────────────────────────
  function enhance(el) {
    if (el.hasAttribute('data-money-ready') || el.tagName !== 'INPUT') return;
    el.setAttribute('data-money-ready', '');
    // The number field's own rules do not apply to grouped text, so the ones this script needs are kept aside.
    if (el.hasAttribute('step')) el.setAttribute('data-money-step', el.getAttribute('step'));
    if (el.hasAttribute('min')) el.setAttribute('data-money-min', el.getAttribute('min'));
    el.removeAttribute('step'); el.removeAttribute('min'); el.removeAttribute('max');
    el.type = 'text';
    el.setAttribute('inputmode', 'decimal');
    el.setAttribute('autocomplete', 'off');

    var startValue = valueProp.get.call(el);

    // Everything that reads or sets .value sees the plain number.
    Object.defineProperty(el, 'value', {
      configurable: true,
      get: function () { return rawOf(el); },
      set: function (v) { paint(el, sanitize(v, decimalsFor(el), allowsNegative(el)), false); }
    });
    Object.defineProperty(el, 'valueAsNumber', {
      configurable: true,
      get: function () { var n = parseFloat(rawOf(el)); return isNaN(n) ? NaN : n; }
    });

    el.addEventListener('input', function () { paint(el, rawOf(el), true); });
    el.addEventListener('blur', function () { paint(el, rawOf(el), false); });
    // A form that starts at 0.00: typing replaces it instead of landing behind it.
    el.addEventListener('focus', function () {
      var raw = rawOf(el);
      if (raw !== '' && parseFloat(raw) === 0) setTimeout(function () { try { el.select(); } catch (e) { /* not supported */ } }, 0);
    });
    el.addEventListener('paste', function () { setTimeout(function () { paint(el, rawOf(el), false); }, 0); });

    var form = el.form;
    if (form && !form._moneyBound) {
      form._moneyBound = true;
      // The browser builds the submission from the displayed text; send the plain numbers instead.
      form.addEventListener('formdata', function (e) {
        var inputs = form.querySelectorAll('input[data-money-ready][name]');
        for (var i = 0; i < inputs.length; i++) {
          if (inputs[i].disabled) continue;
          if (e.formData.getAll(inputs[i].name).length === 1) e.formData.set(inputs[i].name, rawOf(inputs[i]));
        }
      });
      form.addEventListener('reset', function () { setTimeout(function () { repaintAll(form); }, 0); });
    }

    combine(el);
    valueProp.set.call(el, format(sanitize(startValue, decimalsFor(el), allowsNegative(el)), currencyOf(el)));
  }

  function enhanceAll(root) {
    var scope = root && root.querySelectorAll ? root : document;
    var list = scope.querySelectorAll('input[data-money]:not([data-money-ready])');
    for (var i = 0; i < list.length; i++) enhance(list[i]);
    if (scope !== document && scope.matches && scope.matches('input[data-money]:not([data-money-ready])')) enhance(scope);
  }

  function boot() {
    enhanceAll(document);
    // Inputs that arrive later (a modal loaded by htmx, a row added by a script) are picked up too.
    new MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        var added = records[i].addedNodes;
        for (var j = 0; j < added.length; j++) if (added[j].nodeType === 1) enhanceAll(added[j]);
      }
    }).observe(document.documentElement, { childList: true, subtree: true });
  }

  window.TMRMoney = { enhance: enhanceAll, format: format, sanitize: sanitize };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
