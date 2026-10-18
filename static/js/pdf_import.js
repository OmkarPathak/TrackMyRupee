// Reads a bank statement PDF in the browser and swaps it for a CSV in the file input, so the PDF (and its
// password) never leave the device. Loaded only on the import page; pdf.js itself is fetched only when a PDF is chosen.
(function () {
    var input = document.getElementById('formFile');
    if (!input) return;
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

    // Header words that sit next to each other (e.g. "Value" "Date") are joined into one column.
    function headerCells(line) {
        var cells = [];
        line.items.forEach(function (it) {
            var last = cells[cells.length - 1];
            if (last && it.x - (last.x + last.w) < 6) {
                last.s += ' ' + it.s; last.w = it.x + it.w - last.x;
            } else {
                cells.push({s: it.s, x: it.x, w: it.w});
            }
        });
        return cells;
    }

    function isHeader(line) {
        var hits = line.items.filter(function (i) { return HEAD.test(i.s); }).length;
        return hits >= 3 && /date/i.test(line.items.map(function (i) { return i.s; }).join(' '));
    }

    function nearest(cols, it) {
        var c = it.x + it.w / 2, best = 0, d = Infinity;
        cols.forEach(function (col, n) {
            var dist = Math.min(Math.abs(c - (col.x + col.w / 2)), Math.abs(it.x - col.x), Math.abs(it.x + it.w - col.x - col.w));
            if (dist < d) { d = dist; best = n; }
        });
        return best;
    }

    function csvCell(v) {
        v = String(v || '');
        return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
    }

    async function toCsv(doc) {
        var cols = null, rows = [];
        for (var p = 1; p <= doc.numPages; p++) {
            var lines = await pageLines(await doc.getPage(p));
            for (var l = 0; l < lines.length; l++) {
                var line = lines[l];
                if (isHeader(line)) {
                    if (!cols) cols = headerCells(line);
                    continue;
                }
                if (!cols) continue;
                var cells = cols.map(function () { return []; });
                line.items.forEach(function (it) { cells[nearest(cols, it)].push(it.s); });
                cells = cells.map(function (c) { return c.join(' '); });
                if (DATE.test(cells[0])) rows.push(cells);
                else if (rows.length && cells[0] === '') {          // a narration that wrapped onto a second line
                    var prev = rows[rows.length - 1];
                    cells.forEach(function (c, n) { if (c && !/^[\d,.\-]+(?:\s?(?:Dr|Cr))?$/i.test(c)) prev[n] = (prev[n] + ' ' + c).trim(); });
                }
            }
        }
        if (!cols || !rows.length) return null;
        return [cols.map(function (c) { return c.s; })].concat(rows).map(function (r) { return r.map(csvCell).join(','); }).join('\n');
    }

    input.addEventListener('change', async function () {
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
