/* Year in Review ("Wrapped"): the card viewer and the share sheet.
   Cards are plain HTML that scale with their width (cqw), so sharing draws a copy at 1080 x 1920 off screen and
   turns it into a PNG with html2canvas, loaded only when someone shares. Nothing leaves the device. */
(function () {
    'use strict';
    var root = document.getElementById('wr');
    if (!root) return;

    var text = {};
    try { text = JSON.parse(document.getElementById('wr-text').textContent); } catch (e) { /* defaults below */ }
    var cards = Array.prototype.slice.call(root.querySelectorAll('.wr-card'));
    var progress = document.getElementById('wrProgress');
    var prev = document.getElementById('wrPrev'), next = document.getElementById('wrNext');
    var sheet = document.getElementById('wrSheet');
    var statusEl = document.getElementById('wrStatus');
    var index = 0, busy = false;

    // ---------- viewer ----------
    cards.forEach(function (card, i) {
        var bar = document.createElement('button');
        bar.type = 'button';
        bar.setAttribute('aria-label', card.getAttribute('data-title') || String(i + 1));
        bar.addEventListener('click', function () { show(i); });
        progress.appendChild(bar);
    });

    function show(i) {
        index = Math.max(0, Math.min(cards.length - 1, i));
        cards.forEach(function (c, n) { c.classList.toggle('on', n === index); });
        Array.prototype.forEach.call(progress.children, function (b, n) { b.classList.toggle('done', n <= index); });
        prev.disabled = index === 0;
        next.disabled = index === cards.length - 1;
    }
    prev.addEventListener('click', function () { show(index - 1); });
    next.addEventListener('click', function () { show(index + 1); });

    document.addEventListener('keydown', function (e) {
        if (sheet.classList.contains('open')) { if (e.key === 'Escape') closeSheet(); return; }
        if (e.key === 'ArrowRight' || e.key === ' ') { e.preventDefault(); show(index + 1); }
        else if (e.key === 'ArrowLeft') { show(index - 1); }
    });

    // tap the right 2/3 of the card for the next one, the left third for the previous; swipe works too
    var deck = document.getElementById('wrDeck'), startX = null, startY = null;
    deck.addEventListener('click', function (e) {
        var box = deck.getBoundingClientRect();
        show(index + (e.clientX - box.left < box.width / 3 ? -1 : 1));
    });
    deck.addEventListener('touchstart', function (e) { startX = e.touches[0].clientX; startY = e.touches[0].clientY; }, { passive: true });
    deck.addEventListener('touchend', function (e) {
        if (startX === null) return;
        var dx = e.changedTouches[0].clientX - startX, dy = e.changedTouches[0].clientY - startY;
        startX = startY = null;
        if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) { e.preventDefault(); show(index + (dx < 0 ? 1 : -1)); }
    });
    show(0);

    // ---------- share sheet ----------
    var hide = document.getElementById('wrHide'), money = document.getElementById('wrMoney'), nameBox = document.getElementById('wrName');
    var firstName = root.getAttribute('data-name') || '';
    if (!firstName) { nameBox.checked = false; nameBox.closest('.wr-opt').style.display = 'none'; }

    var all = document.getElementById('wrAll'), moneyRow = document.getElementById('wrMoneyRow');
    function chosen() {
        // the card in view; with "Share all cards", the whole set (the Money story only if asked for)
        if (!all.checked) return [cards[index]];
        return cards.filter(function (c) { return money.checked || !c.hasAttribute('data-money'); });
    }
    function setStatus(msg) { statusEl.textContent = msg || ''; }
    function openSheet() { setStatus(''); moneyRow.style.display = all.checked ? '' : 'none'; sheet.classList.add('open'); }
    function closeSheet() { if (!busy) sheet.classList.remove('open'); }
    document.getElementById('wrShare').addEventListener('click', openSheet);
    document.getElementById('wrCancel').addEventListener('click', closeSheet);
    sheet.addEventListener('click', function (e) { if (e.target === sheet) closeSheet(); });
    all.addEventListener('change', function () { moneyRow.style.display = all.checked ? '' : 'none'; });

    // ---------- making the images ----------
    function loadHtml2Canvas() {
        if (window.html2canvas) return Promise.resolve();
        if (window._wrH2c) return window._wrH2c;
        window._wrH2c = new Promise(function (resolve, reject) {
            var s = document.createElement('script');
            s.src = 'https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js';
            s.onload = resolve;
            s.onerror = function () { window._wrH2c = null; reject(new Error('html2canvas')); };
            document.head.appendChild(s);
        });
        return window._wrH2c;
    }

    function render(card) {
        var holder = document.getElementById('wrExport');
        var copy = card.cloneNode(true);
        copy.classList.add('on');
        copy.setAttribute('data-hide', hide.checked ? '1' : '0');
        copy.setAttribute('data-name', nameBox.checked && firstName ? '1' : '0');
        holder.appendChild(copy);
        return window.html2canvas(copy, { width: 1080, height: 1920, scale: 1, backgroundColor: null, useCORS: true, logging: false })
            .then(function (canvas) {
                holder.removeChild(copy);
                return new Promise(function (resolve, reject) {
                    canvas.toBlob(function (blob) { blob ? resolve(blob) : reject(new Error('blob')); }, 'image/png');
                });
            }, function (err) { holder.removeChild(copy); throw err; });
    }

    function makeFiles() {
        var list = chosen();
        if (!list.length) { setStatus(text.pickOne || ''); return Promise.resolve(null); }
        setStatus(text.saving || '');
        busy = true;
        var year = root.getAttribute('data-year');
        return loadHtml2Canvas()
            .then(function () { return (document.fonts && document.fonts.ready) || null; })
            .then(function () {
                var files = [];
                return list.reduce(function (chain, card, i) {
                    return chain.then(function () { return render(card); }).then(function (blob) {
                        files.push(new File([blob], 'trackmyrupee-wrapped-' + year + '-' + (i + 1) + '-' + card.getAttribute('data-card') + '.png', { type: 'image/png' }));
                    });
                }, Promise.resolve()).then(function () { return files; });
            })
            .catch(function () { setStatus(text.failed || ''); return null; })
            .then(function (files) { busy = false; return files; });
    }

    function download(files) {
        files.forEach(function (file, i) {
            setTimeout(function () {
                var url = URL.createObjectURL(file), a = document.createElement('a');
                a.href = url; a.download = file.name;
                document.body.appendChild(a); a.click(); document.body.removeChild(a);
                setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
            }, i * 350);
        });
        setStatus(text.done || '');
    }

    document.getElementById('wrDownload').addEventListener('click', function () {
        makeFiles().then(function (files) { if (files) download(files); });
    });
    document.getElementById('wrSend').addEventListener('click', function () {
        makeFiles().then(function (files) {
            if (!files) return;
            if (navigator.canShare && navigator.canShare({ files: files })) {
                navigator.share({ files: files, title: document.title }).then(function () { setStatus(text.done || ''); }, function (err) {
                    if (err && err.name === 'AbortError') return;   // the person cancelled
                    download(files);                                // the browser wanted a fresh tap: save instead
                });
            } else {
                download(files);   // no share sheet on this device: save the images instead
            }
        });
    });
})();
