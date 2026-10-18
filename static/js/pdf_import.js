// Reads a bank statement PDF in the browser and swaps it for a CSV in the file input, so the PDF (and its
// password) never leave the device. Loaded only on the import page; pdf.js itself is fetched only when a PDF is chosen.
(function () {
    var input = document.getElementById('formFile');
    window.TMRPdfStatement = {pagesToCsv: function () { return pagesToCsv.apply(null, arguments); }};
    var note = document.getElementById('pdf-import-note');
    var msg = function (k) { return (note && note.dataset[k]) || ''; };
    var DATE = /^\d{1,2}[\/\-. ](?:[A-Za-z]{3,9}|\d{1,2})[\/\-. ]\d{2,4}$/;
    var HEAD = /date|narration|description|particulars|details|withdrawal|deposit|debit|credit|balance|amount|ref|chq/i;

    function say(text, bad) {
        if (!note) return;
        note.textContent = text;
        note.className = 'form-text ' + (bad ? 'text-danger' : 'text-muted');
    }

    async function open(file) {
        var pdfjs = await import(input.dataset.pdfjs);
        pdfjs.GlobalWorkerOptions.workerSrc = input.dataset.pdfjsWorker;
        var data = new Uint8Array(await file.arrayBuffer());
        var password;
        for (;;) {
            try {
                return await pdfjs.getDocument({data: data.slice(), password: password}).promise;
            } catch (e) {
                if (e && e.name === 'PasswordException') {
                    password = window.prompt(e.code === 2 ? msg('wrong') : msg('ask'));
                    if (password === null) throw new Error('cancelled');
                } else {
                    throw e;
                }
            }
        }
    }

    async function pageLines(page) {
        var content = await page.getTextContent();
        var items = content.items.filter(function (i) { return i.str && i.str.trim(); })
            .map(function (i) { return {s: i.str.trim(), x: i.transform[4], w: i.width, y: i.transform[5]}; });
        items.sort(function (a, b) { return b.y - a.y || a.x - b.x; });
        var lines = [];
        items.forEach(function (it) {
            var last = lines[lines.length - 1];
            if (last && Math.abs(last.y - it.y) <= 3) last.items.push(it);
            else lines.push({y: it.y, items: [it]});
        });
        lines.forEach(function (l) { l.items.sort(function (a, b) { return a.x - b.x; }); });
        return lines;
    }

    // The heading row, plus any heading words stacked just above or below it ("Value" over "Date"), as columns.
    // Words that overlap sideways belong to one column; words that merely sit next to each other are joined.
    function headerCells(lines, at) {
        var words = [];
        lines.forEach(function (l) {
            if (Math.abs(l.y - lines[at].y) <= 9) l.items.forEach(function (it) { words.push({s: it.s, x: it.x, w: it.w, y: it.y}); });
        });
        words.sort(function (a, b) { return a.x - b.x; });
        var cells = [];
        words.forEach(function (it) {
            var last = cells[cells.length - 1];
            if (last && it.x < last.x + last.w + 1) {                       // stacked words
                last.parts.push(it);
                last.w = Math.max(last.x + last.w, it.x + it.w) - last.x;
            } else if (last && it.x - (last.x + last.w) < 6 && Math.abs(it.y - last.parts[0].y) <= 3) {   // side by side
                last.parts.push(it);
                last.w = it.x + it.w - last.x;
            } else {
                cells.push({x: it.x, w: it.w, parts: [it]});
            }
        });
        cells.forEach(function (c) {
            c.parts.sort(function (a, b) { return b.y - a.y || a.x - b.x; });
            c.s = c.parts.map(function (p) { return p.s; }).join(' ');
        });
        return cells;
    }

    function isHeader(line) {
        var hits = line.items.filter(function (i) { return HEAD.test(i.s); }).length;
        return hits >= 3 && /date/i.test(line.items.map(function (i) { return i.s; }).join(' '));
    }

    // Amounts may be aligned left, centred or right under their heading, so they go to the closest heading by any edge.
    // Text is left-aligned: it belongs to the last column that starts at or before it.
    function nearest(cols, it) {
        if (/^-?[\d,]*\d[.,]\d+(?:\s?(?:Dr|Cr))?$/i.test(it.s)) {
            var c = it.x + it.w / 2, best = 0, d = Infinity;
            cols.forEach(function (col, n) {
                var dist = Math.min(Math.abs(c - (col.x + col.w / 2)), Math.abs(it.x - col.x), Math.abs(it.x + it.w - col.x - col.w));
                if (dist < d) { d = dist; best = n; }
            });
            return best;
        }
        var at = 0;
        cols.forEach(function (col, n) { if (it.x >= col.x - 6) at = n; });
        return at;
    }

    function csvCell(v) {
        v = String(v || '');
        return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
    }

    var NUMBER = /^-?[\d,]+(?:\.\d+)?(?:\s?(?:Dr|Cr))?$/i;
    var CARRIED = /^(balance (?:b\/f|c\/f|forward|brought forward|carried forward)|brought forward|carried forward|b\/f|c\/f|opening balance|closing balance)\b/i;

    function colIndex(cols, test, avoid) {
        for (var n = 0; n < cols.length; n++) if (test.test(cols[n].s) && !(avoid && avoid.test(cols[n].s))) return n;
        return -1;
    }

    // pages: an array of pages, each an array of lines ({y, items:[{s, x, w, y}]}, top to bottom). Returns CSV text or null.
    function pagesToCsv(pages) {
        var cols = null, rows = [], dateAt = 0, descAt = 1, balAt = 0;
        pages.forEach(function (lines) {
            var skip = [];
            lines.forEach(function (line, l) {
                if (!isHeader(line)) return;
                if (!cols) {
                    cols = headerCells(lines, l);
                    dateAt = Math.max(colIndex(cols, /date/i, /value/i), 0);
                    descAt = Math.max(colIndex(cols, /narration|description|particulars|details|remarks/i), 0);
                    balAt = colIndex(cols, /balance/i);
                    if (balAt < 0) balAt = cols.length - 1;
                }
                lines.forEach(function (o) { if (Math.abs(o.y - line.y) <= 9) skip.push(o); });
            });
            if (!cols) return;
            var lastY = null, ended = false;                 // a TOTAL line closes the table on this page
            lines.forEach(function (line) {
                if (ended || skip.indexOf(line) >= 0) return;
                var cells = cols.map(function () { return []; });
                line.items.forEach(function (it) { cells[nearest(cols, it)].push(it.s); });
                cells = cells.map(function (c) { return c.join(' '); });
                if (cells.some(function (c) { return /^total$/i.test(c); })) { ended = true; return; }
                var dated = DATE.test(cells[dateAt]);
                // Banks leave the date off a row when it repeats the one above, so a row with a description and a balance starts one too.
                var undated = !cells[dateAt] && cells[descAt] && NUMBER.test(cells[balAt]) && rows.length;
                if (dated || undated) {
                    if (undated) cells[dateAt] = rows[rows.length - 1][dateAt];
                    cells.drop = CARRIED.test(cells[descAt]);        // a balance carried over is not a transaction
                    rows.push(cells);
                    lastY = line.y;
                } else if (!cells[dateAt] && cells[descAt] && rows.length && lastY !== null && lastY - line.y <= 20) {   // a narration that wrapped
                    var prev = rows[rows.length - 1];
                    prev[descAt] = (prev[descAt] + ' ' + cells[descAt]).trim();
                    lastY = line.y;
                }
            });
        });
        rows = rows.filter(function (r) { return !r.drop; });
        if (!cols || !rows.length) return null;
        return [cols.map(function (c) { return c.s; })].concat(rows).map(function (r) { return r.map(csvCell).join(','); }).join('\n');
    }

    async function toCsv(doc) {
        var pages = [];
        for (var p = 1; p <= doc.numPages; p++) pages.push(await pageLines(await doc.getPage(p)));
        return pagesToCsv(pages);
    }

    if (input) input.addEventListener('change', async function () {
        var file = input.files && input.files[0];
        if (!file || !/\.pdf$/i.test(file.name)) return;
        say(msg('reading'));
        try {
            var csv = await toCsv(await open(file));
            if (!csv) { input.value = ''; say(msg('none'), true); return; }
            var out = new DataTransfer();
            out.items.add(new File([csv], file.name.replace(/\.pdf$/i, '') + '.csv', {type: 'text/csv'}));
            input.files = out.files;
            say(msg('done'));
        } catch (e) {
            input.value = '';
            say(e && e.message === 'cancelled' ? '' : msg('failed'), true);
        }
    });
})();
