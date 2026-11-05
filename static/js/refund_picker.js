/* The "Refund for" part of the income form: one search box over the expenses and capital events a refund can pay back.

   The list opens with "Not linked to anything" first (the default: the money counts as income), then the latest few
   expenses and capital events together, newest first. Typing searches both kinds. Choosing a row fills the matching
   hidden field (refund_for_expense or refund_for_capital_event) and empties the other; the server checks the link is
   the person's own. Choosing "Not linked", or the x on the chosen row, unlinks it.

   Markup: templates/expenses/income_form.html (#refund-for-container). Endpoint: refund_target_search. */
(function () {
  'use strict';

  var box = document.getElementById('refund-for-container');
  if (!box) return;

  var url = box.getAttribute('data-search-url');
  var expenseField = document.getElementById('id_refund_for_expense');
  var eventField = document.getElementById('id_refund_for_capital_event');
  var chosen = document.getElementById('refund-chosen');
  var picker = document.getElementById('refund-picker');
  var search = document.getElementById('refund-search');
  var results = document.getElementById('refund-results');
  var description = document.getElementById('id_description');
  if (!url || !expenseField || !eventField || !chosen || !picker || !search || !results) return;

  function label(name, fallback) { return box.getAttribute('data-label-' + name) || fallback; }
  var labels = {
    recent: label('recent', 'Recent'), matches: label('matches', 'Matches'),
    none: label('none', 'No matches'), notLinked: label('not-linked', 'Not linked to anything'),
    notLinkedMeta: label('not-linked-meta', 'Counts as income'),
    expense: label('expense', 'Expense'), event: label('event', 'Capital event'),
    refunded: label('refunded', 'refunded'), refundPrefix: label('refund-prefix', 'Refund'),
    failed: label('failed', 'Search is not available right now. Try again.')
  };

  var NOT_LINKED = { kind: 'none' };
  var rows = [], active = -1, timer = null, controller = null, cache = {}, seq = 0;

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function isLinked() { return !!(expenseField.value || eventField.value); }
  function kindLabel(kind) { return kind === 'event' ? labels.event : labels.expense; }

  // ── The chosen row ──────────────────────────────────────────────────────
  function tag(kind) { return el('span', 'tmr-picker-kind' + (kind === 'event' ? ' tmr-picker-kind--event' : ''), kindLabel(kind)); }

  function paintChosen(row) {
    var main = chosen.querySelector('.tmr-picker-chosen-main');
    var meta = chosen.querySelector('.tmr-picker-chosen-meta');
    main.textContent = row.description || '—';
    meta.textContent = '';
    meta.appendChild(tag(row.kind));
    meta.appendChild(document.createTextNode([row.category, row.date_label, row.amount_label].filter(Boolean).join(' · ')));
    chosen.hidden = false;
    picker.hidden = true;
  }

  function setLink(row) {
    expenseField.value = row.kind === 'expense' ? String(row.id) : '';
    eventField.value = row.kind === 'event' ? String(row.id) : '';
  }

  function choose(row) {
    closeResults();
    search.value = '';
    if (row.kind === 'none') {
      setLink(NOT_LINKED);
      chosen.hidden = true;
      picker.hidden = false;
      return;
    }
    setLink(row);
    paintChosen(row);
    if (description && !description.value.trim() && row.description) {
      description.value = labels.refundPrefix + ': ' + row.description;
    }
  }

  chosen.querySelector('.tmr-picker-clear').addEventListener('click', function () {
    choose(NOT_LINKED);
    search.focus({ preventScroll: true });
  });

  // ── The list ────────────────────────────────────────────────────────────
  function closeResults() {
    seq++;                                  // a search still on its way must not reopen the list
    results.hidden = true;
    search.setAttribute('aria-expanded', 'false');
    search.removeAttribute('aria-activedescendant');
    active = -1;
  }

  function highlight(i) {
    var items = results.querySelectorAll('.tmr-picker-row');
    for (var n = 0; n < items.length; n++) items[n].classList.toggle('is-active', n === i);
    active = i;
    if (items[i]) {
      search.setAttribute('aria-activedescendant', items[i].id);
      items[i].scrollIntoView({ block: 'nearest' });
    }
  }

  function rowButton(row, i) {
    var b = el('button', 'tmr-picker-row' + (row.kind === 'none' ? ' tmr-picker-row--none' : ''));
    b.type = 'button';
    b.id = 'refund-opt-' + i;
    b.setAttribute('role', 'option');
    var left = el('span', 'tmr-picker-main');
    var side = el('span', 'tmr-picker-side');
    if (row.kind === 'none') {
      left.appendChild(el('span', 'tmr-picker-desc', labels.notLinked));
      left.appendChild(el('span', 'tmr-picker-meta', labels.notLinkedMeta));
      if (!isLinked()) side.appendChild(el('i', 'bi bi-check-lg'));
    } else {
      left.appendChild(el('span', 'tmr-picker-desc', row.description || '—'));
      var meta = el('span', 'tmr-picker-meta');
      meta.appendChild(tag(row.kind));
      meta.appendChild(document.createTextNode([row.category, row.date_label].filter(Boolean).join(' · ')));
      left.appendChild(meta);
      side.appendChild(el('span', 'tmr-picker-amount', row.amount_label));
      if (row.refunded) side.appendChild(el('span', 'tmr-picker-refunded', '↩ ' + row.refunded_label + ' ' + labels.refunded));
    }
    b.appendChild(left);
    b.appendChild(side);
    b.addEventListener('mousedown', function (e) { e.preventDefault(); });   // keep focus in the box
    b.addEventListener('click', function () { choose(row); });
    b.addEventListener('mousemove', function () { if (active !== i) highlight(i); });
    return b;
  }

  function render(data) {
    var found = data.results || [];
    rows = [NOT_LINKED].concat(found);
    results.textContent = '';
    results.appendChild(rowButton(NOT_LINKED, 0));
    results.appendChild(el('div', 'tmr-picker-head', data.recent ? labels.recent : labels.matches));
    if (!found.length) results.appendChild(el('div', 'tmr-picker-empty', labels.none));
    found.forEach(function (row, n) { results.appendChild(rowButton(row, n + 1)); });
    results.hidden = false;
    search.setAttribute('aria-expanded', 'true');
    active = -1;
  }

  function showFailure() {
    rows = [NOT_LINKED];
    results.textContent = '';
    results.appendChild(rowButton(NOT_LINKED, 0));
    results.appendChild(el('div', 'tmr-picker-empty', labels.failed));
    results.hidden = false;
    search.setAttribute('aria-expanded', 'true');
    active = -1;
  }

  function load(query) {
    var mine = ++seq;                       // only the newest request may draw; closing the list bumps this too
    if (controller) controller.abort();
    if (cache[query]) { render(cache[query]); return; }
    controller = typeof AbortController === 'function' ? new AbortController() : null;
    fetch(url + '?q=' + encodeURIComponent(query), {
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json' },
      signal: controller ? controller.signal : undefined
    }).then(function (r) { if (!r.ok) throw new Error('search failed'); return r.json(); })
      .then(function (data) {
        if (mine !== seq || search.value.trim() !== query) return;
        cache[query] = data;                // only a good answer is kept: a failure is tried again next time
        render(data);
      })
      .catch(function (err) {
        if (err && err.name === 'AbortError') return;   // a newer keystroke took over
        if (mine === seq) showFailure();
      });
  }

  search.addEventListener('focus', function () { load(search.value.trim()); });
  search.addEventListener('input', function () {
    clearTimeout(timer);
    timer = setTimeout(function () { load(search.value.trim()); }, 180);
  });
  search.addEventListener('keydown', function (e) {
    var count = rows.length;
    if (e.key === 'ArrowDown') { e.preventDefault(); if (results.hidden) load(search.value.trim()); else if (count) highlight((active + 1) % count); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); if (count) highlight((active - 1 + count) % count); }
    else if (e.key === 'Enter') {
      if (!results.hidden) { e.preventDefault(); if (active >= 0 && rows[active]) choose(rows[active]); }
    } else if (e.key === 'Escape') { if (!results.hidden) { e.preventDefault(); e.stopPropagation(); closeResults(); } }
  });
  document.addEventListener('click', function (e) { if (!picker.contains(e.target)) closeResults(); });

  // ── Start from what the form already holds (an edit, or "Add refund" on an expense or capital event) ──
  var initial = null;
  var initialEl = document.getElementById('refund-initial');
  if (initialEl) { try { initial = JSON.parse(initialEl.textContent); } catch (e) { initial = null; } }
  if (initial && isLinked()) paintChosen(initial);
  else { chosen.hidden = true; picker.hidden = false; }
})();
