/* Budget page behaviour: the adjust-limit dialog/sheet, bulk limits, reset, optimistic updates with
 * rollback, and the retry toast. The page itself is server-rendered and swapped by htmx; every
 * handler is delegated on document so it survives those swaps. */
(function () {
    'use strict';
    if (window.__bdxInit) return;
    window.__bdxInit = true;

    var $ = function (sel, root) { return (root || document).querySelector(sel); };
    var shell = function () { return $('#budget-shell'); };
    var T = function () { return ($('#bdx-i18n') || {}).dataset || {}; };
    var fmt = function (tpl, vars) { return String(tpl || '').replace(/%\((\w+)\)s/g, function (_, k) { return vars[k] === undefined ? '' : vars[k]; }); };
    var csrf = function () { return document.body.getAttribute('data-csrf-token') || ''; };

    /* ---- numbers ---- */
    var numberFormat = null;
    function money(n) {
        var t = T();
        if (!numberFormat) {
            try { numberFormat = new Intl.NumberFormat(t.locale || 'en-IN', { maximumFractionDigits: 2 }); }
            catch (e) { numberFormat = { format: function (v) { return String(v); } }; }
        }
        return (t.symbol || '') + numberFormat.format(n);
    }
    // "₹ 5,000.50", "५०००" and "5000" all parse; blank is null; anything else is NaN
    function parseAmount(text) {
        var s = String(text || '').trim();
        if (!s) return null;
        s = s.replace(/[०-९]/g, function (d) { return String(d.charCodeAt(0) - 0x0966); })
             .replace(/[^\d.,-]/g, '').replace(/,/g, '');
        if (!/^-?\d+(\.\d{1,2})?$/.test(s) && !/^-?\d*\.\d{1,2}$/.test(s)) return NaN;
        return parseFloat(s);
    }

    /* ---- toast ---- */
    var toastTimer = null;
    function toast(message, actionLabel, action) {
        var box = $('#bdx-toast'); if (!box) return;
        $('#bdx-toast-msg').textContent = message;
        var btn = $('#bdx-toast-action');
        btn.hidden = !actionLabel;
        btn.textContent = actionLabel || '';
        btn.onclick = function () { hideToast(); if (action) action(); };
        box.hidden = false;
        clearTimeout(toastTimer);
        if (!actionLabel) toastTimer = setTimeout(hideToast, 4000);
    }
    function hideToast() { var box = $('#bdx-toast'); if (box) box.hidden = true; }

    /* ---- API ---- */
    function api(url, method, body) {
        return fetch(url, {
            method: method, credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRFToken': csrf() },
            body: JSON.stringify(body)
        }).then(function (res) {
            return res.json().catch(function () { return {}; }).then(function (data) {
                if (!res.ok || data.success === false) {
                    var err = new Error(data.error || T().failed); err.code = data.code; throw err;
                }
                return data;
            });
        }, function () { throw new Error(T().offline); });
    }
    function refresh() {
        var el = shell();
        if (window.htmx && el) {
            window.htmx.ajax('GET', el.getAttribute('data-url'), { target: '#budget-shell', swap: 'outerHTML show:none' });
        } else { window.location.reload(); }
    }

    /* ---- the adjust-limit sheet ---- */
    var state = null;   // { id, name, limit, spent, card, every }

    function cardData(el) {
        var card = el.closest('[data-bdx-cat]');
        var limit = card.getAttribute('data-limit');
        return {
            card: card, id: parseInt(card.getAttribute('data-id'), 10), name: card.getAttribute('data-name'),
            limit: limit === '' ? null : parseFloat(limit), spent: parseFloat(card.getAttribute('data-spent') || '0')
        };
    }
    function fundCandidates() {
        var node = $('#bdx-data'), rows = [];
        try { rows = JSON.parse(node.textContent) || []; } catch (e) { rows = []; }
        return rows.filter(function (r) { return r.id !== state.id && r.limit > 0; });
    }
    function currentScope() { var r = $('input[name="bdx-scope"]:checked'); return r ? r.value : 'this_month'; }
    function monthLabel() { return shell().getAttribute('data-month-label'); }

    function openSheet(trigger, scope) {
        var data = cardData(trigger), t = T(), el = shell();
        state = data;
        state.every = el.getAttribute('data-every-month') === '1';
        $('#bdx-sheet-title').textContent = data.limit === null ? t.titleSet : t.titleAdjust;
        $('#bdx-sheet-cat').textContent = data.name;
        $('#bdx-amount').value = data.limit === null ? '' : String(data.limit);
        $('#bdx-error').hidden = true;
        $('#bdx-remove').hidden = data.limit === null;

        var every = $('#bdx-scope-every'), thisOnly = $('#bdx-scope-this');
        every.disabled = !state.every;
        $('#bdx-scope-hint').hidden = state.every;
        (state.every && scope === 'every_month' ? every : thisOnly).checked = true;

        var cands = fundCandidates(), select = $('#bdx-fund-from');
        select.innerHTML = '';
        var pick = document.createElement('option'); pick.value = ''; pick.textContent = t.fundPick; select.appendChild(pick);
        cands.forEach(function (r) {
            var o = document.createElement('option'); o.value = r.id;
            o.textContent = fmt(t.fundRoom, { name: r.name, room: money(Math.max(r.limit - r.spent, 0)) });
            o.setAttribute('data-room', Math.max(r.limit - r.spent, 0));
            select.appendChild(o);
        });
        var fund = $('#bdx-fund'); fund.open = false;
        fund.hidden = false;
        $('#bdx-fund-note').textContent = cands.length ? '' : t.fundNone;
        select.disabled = !cands.length;
        updatePreview();
        window.bootstrap.Modal.getOrCreateInstance($('#bdx-sheet')).show();
    }

    function updatePreview() {
        if (!state) return;
        var t = T(), amount = parseAmount($('#bdx-amount').value), scope = currentScope();
        var every = scope === 'every_month', name = state.name, month = monthLabel();
        var text = '', err = '';
        if (Number.isNaN(amount)) { err = t.eInvalid; }
        else if (amount !== null && amount < 0) { err = t.eNegative; }
        else if (amount === null) { text = fmt(every ? t.pRmEvery : t.pRmThis, { name: name, month: month }); }
        else if (state.limit === null) { text = fmt(every ? t.pNewEvery : t.pNewThis, { name: name, to: money(amount), month: month }); }
        else { text = fmt(every ? t.pEvery : t.pThis, { name: name, from: money(state.limit), to: money(amount), month: month }); }

        var src = $('#bdx-fund-from'), delta = (amount || 0) - (state.limit || 0);
        if (!err && delta > 0 && src.value) {
            var opt = src.selectedOptions[0], room = parseFloat(opt.getAttribute('data-room'));
            var cand = fundCandidates().filter(function (r) { return String(r.id) === src.value; })[0];
            if (delta > room) { err = t.eFund; }
            else if (cand) { text += ' · ' + fmt(t.pFund, { src: cand.name, from: money(cand.limit), to: money(cand.limit - delta) }); }
        }
        $('#bdx-fund-note').textContent = delta > 0 && src.options.length > 1 ? fmt(T().fundNote, { name: name, amount: money(delta) }) : (src.options.length > 1 ? '' : T().fundNone);
        var errBox = $('#bdx-error');
        errBox.textContent = err; errBox.hidden = !err;
        $('#bdx-preview').textContent = err ? '' : text;
        $('#bdx-save').disabled = !!err;
        return { amount: amount, err: err };
    }

    /* optimistic update of one card; returns a function that undoes it */
    function applyOptimistic(card, newLimit, spent) {
        var snapshot = card.innerHTML, classes = card.className, limitAttr = card.getAttribute('data-limit');
        card.classList.add('is-saving'); card.style.opacity = '.6';
        card.setAttribute('data-limit', newLimit === null ? '' : String(newLimit));
        var fill = card.querySelector('.bdx-bar-fill');
        if (fill && newLimit) { fill.style.width = Math.min(100, spent / newLimit * 100).toFixed(1) + '%'; }
        var pct = card.querySelector('.bdx-pct');
        if (pct && newLimit) { pct.textContent = Math.round(spent / newLimit * 100) + '%'; }
        var left = card.querySelector('[data-bdx-left]');
        if (left && newLimit) { left.textContent = money(Math.max(newLimit - spent, 0)); }
        var meta = card.querySelector('[data-bdx-meta]');
        if (meta && newLimit) { meta.textContent = money(spent) + ' / ' + money(newLimit); }
        return function undo() {
            card.innerHTML = snapshot; card.className = classes; card.style.opacity = '';
            card.setAttribute('data-limit', limitAttr);
        };
    }

    function submitSheet(event) {
        event.preventDefault();
        var check = updatePreview();
        if (!check || check.err) return;
        var amount = check.amount, scope = currentScope(), s = state, el = shell(), month = el.getAttribute('data-month');
        var src = $('#bdx-fund-from'), delta = (amount || 0) - (s.limit || 0);
        var funded = !!(amount !== null && delta > 0 && src.value && $('#bdx-fund').open);
        var request = funded
            ? function () { return api(T().apiRebalance, 'POST', { month: month, scope: scope, moves: [{ from_category: parseInt(src.value, 10), to_category: s.id, amount: delta }] }); }
            : function () { return api(T().apiLimit, 'PUT', { category_id: s.id, month: month, amount: amount, scope: scope }); };
        window.bootstrap.Modal.getOrCreateInstance($('#bdx-sheet')).hide();
        run(request, s.card, amount, s.spent, T().saved);
    }

    function run(request, card, newLimit, spent, okMessage) {
        var undo = card ? applyOptimistic(card, newLimit, spent) : function () {};
        request().then(function () {
            toast(okMessage); refresh();
        }).catch(function (err) {
            undo();
            toast(err.message || T().failed, T().retry, function () { run(request, card, newLimit, spent, okMessage); });
        });
    }

    function removeLimit() {
        if (!state) return;
        var s = state, scope = currentScope(), month = shell().getAttribute('data-month');
        window.bootstrap.Modal.getOrCreateInstance($('#bdx-sheet')).hide();
        run(function () { return api(T().apiLimit, 'PUT', { category_id: s.id, month: month, amount: null, scope: scope }); },
            s.card, null, s.spent, T().saved);
    }

    function resetOverride(button) {
        var d = cardData(button), month = shell().getAttribute('data-month');
        button.disabled = true;
        api(T().apiReset, 'POST', { category_id: d.id, month: month }).then(function () {
            toast(T().resetDone); refresh();
        }).catch(function (err) {
            button.disabled = false;
            toast(err.message || T().failed, T().retry, function () { resetOverride(button); });
        });
    }

    /* ---- bulk ---- */
    function openBulk() {
        var node = $('#bdx-data'), rows = [], t = T(), box = $('#bdx-bulk-rows');
        try { rows = JSON.parse(node.textContent) || []; } catch (e) { rows = []; }
        rows = rows.filter(function (r) { return !(r.limit > 0); });
        box.innerHTML = '';
        if (!rows.length) { box.textContent = t.bulkEmpty; }
        rows.forEach(function (r) {
            var row = document.createElement('div'); row.className = 'bdx-bulk-row';
            var label = document.createElement('label'); label.className = 'bdx-cat-name'; label.textContent = r.name;
            label.setAttribute('for', 'bdx-bulk-' + r.id);
            var wrap = document.createElement('div'); wrap.className = 'bdx-input';
            var input = document.createElement('input'); input.type = 'text'; input.inputMode = 'decimal';
            input.id = 'bdx-bulk-' + r.id; input.setAttribute('data-id', r.id);
            input.setAttribute('aria-label', fmt(t.bulkLabel, { name: r.name }));
            wrap.appendChild(input); row.appendChild(label); row.appendChild(wrap); box.appendChild(row);
        });
        $('#bdx-bulk-error').hidden = true;
        $('#bdx-bulk-save').disabled = !rows.length;
        window.bootstrap.Modal.getOrCreateInstance($('#bdx-bulk')).show();
    }

    function submitBulk(event) {
        event.preventDefault();
        var inputs = Array.prototype.slice.call(document.querySelectorAll('#bdx-bulk-rows input'));
        var el = shell(), month = el.getAttribute('data-month');
        var scope = el.getAttribute('data-every-month') === '1' ? 'every_month' : 'this_month';
        var jobs = [], bad = false;
        inputs.forEach(function (i) {
            var v = parseAmount(i.value);
            if (v === null) return;
            if (Number.isNaN(v) || v < 0) { bad = true; i.setAttribute('aria-invalid', 'true'); return; }
            i.removeAttribute('aria-invalid');
            jobs.push({ category_id: parseInt(i.getAttribute('data-id'), 10), month: month, amount: v, scope: scope });
        });
        var err = $('#bdx-bulk-error');
        if (bad) { err.textContent = T().eInvalid; err.hidden = false; return; }
        if (!jobs.length) { window.bootstrap.Modal.getOrCreateInstance($('#bdx-bulk')).hide(); return; }
        $('#bdx-bulk-save').disabled = true;
        var failed = 0;
        jobs.reduce(function (chain, job) {
            return chain.then(function () { return api(T().apiLimit, 'PUT', job); }).catch(function () { failed += 1; });
        }, Promise.resolve()).then(function () {
            $('#bdx-bulk-save').disabled = false;
            if (failed) { err.textContent = T().bulkPartial; err.hidden = false; refresh(); return; }
            window.bootstrap.Modal.getOrCreateInstance($('#bdx-bulk')).hide();
            toast(T().bulkSaved); refresh();
        });
    }

    /* ---- smart suggestion ---- */
    function reviewSuggestion(button) {
        var card = document.querySelector('[data-bdx-cat][data-id="' + button.getAttribute('data-needy') + '"]');
        if (!card) return;
        openSheet(card, 'this_month');
        var extra = parseFloat(button.getAttribute('data-amount')) || 0;
        $('#bdx-amount').value = String((state.limit || 0) + extra);
        var fund = $('#bdx-fund'); fund.open = true;
        $('#bdx-fund-from').value = button.getAttribute('data-donor');
        updatePreview();
    }
    function dismissSuggestion(button) {
        var box = button.closest('[data-bdx-suggestion]');
        api(T().apiDismiss, 'POST', { key: box.getAttribute('data-key') }).then(function () {
            box.remove();
        }).catch(function (err) { toast(err.message || T().failed); });
    }

    /* ---- wiring ---- */
    document.addEventListener('click', function (e) {
        var t = e.target;
        var adjust = t.closest('[data-bdx-adjust], [data-bdx-edit]');
        if (adjust) { e.preventDefault(); openSheet(adjust, adjust.getAttribute('data-scope')); return; }
        var suggest = t.closest('[data-bdx-suggest]');
        if (suggest) { e.preventDefault(); reviewSuggestion(suggest); return; }
        var dismiss = t.closest('[data-bdx-dismiss]');
        if (dismiss) { e.preventDefault(); dismissSuggestion(dismiss); return; }
        var reset = t.closest('[data-bdx-reset]');
        if (reset) { e.preventDefault(); resetOverride(reset); return; }
        if (t.closest('[data-bdx-bulk]')) { e.preventDefault(); openBulk(); return; }
        var more = t.closest('[data-bdx-untouched]');
        if (more) {
            var el = shell(), open = el.getAttribute('data-collapsed') === '1';
            el.setAttribute('data-collapsed', open ? '0' : '1');
            more.setAttribute('aria-expanded', open ? 'true' : 'false');
            more.textContent = open ? more.getAttribute('data-hide') : more.getAttribute('data-show');
            return;
        }
        if (t.closest('#bdx-toast-close')) { hideToast(); return; }
        if (t.closest('#bdx-remove')) { e.preventDefault(); removeLimit(); }
    });
    document.addEventListener('input', function (e) {
        if (e.target.closest && e.target.closest('#bdx-form')) updatePreview();
    });
    document.addEventListener('change', function (e) {
        if (e.target.closest && e.target.closest('#bdx-form')) updatePreview();
    });
    // capture phase: run (and preventDefault) before the app-wide form handlers in base.html
    document.addEventListener('submit', function (e) {
        if (e.target.id === 'bdx-form') submitSheet(e);
        else if (e.target.id === 'bdx-bulk-form') submitBulk(e);
    }, true);
    document.addEventListener('shown.bs.modal', function (e) {
        if (e.target.id === 'bdx-sheet') { var a = $('#bdx-amount'); a.focus(); a.select(); }
        else if (e.target.id === 'bdx-bulk') { var i = document.querySelector('#bdx-bulk-rows input'); if (i) i.focus(); }
    });
    document.addEventListener('htmx:beforeRequest', function (e) {
        if (e.target && e.target.id === 'budget-shell') e.target.setAttribute('aria-busy', 'true');
    });
    // a failed month fetch keeps the old page, so say so instead of failing silently
    document.addEventListener('htmx:responseError', function () { toast(T().failed); });
    document.addEventListener('htmx:sendError', function () { toast(T().offline); });
})();
