/* Analytics page. Reads the JSON the server put in the page (every number, label and link is already worked out
   there) and draws it. Charts come from the app's own Chart.js; the Sankey is plain SVG. Colours are read from the
   page's CSS tokens, so light and dark both work. A click on a chart opens All Transactions on a mouse; on a phone
   a tap opens a tooltip with a link in it. */
(function () {
  'use strict';

  var charts = [];
  var tipEl = null;
  var sankeyObserver = null;
  var sankeyTimer = null;
  var themeObserver = null;
  var SVG_NS = 'http://www.w3.org/2000/svg';

  function shell() { return document.getElementById('analytics-shell'); }
  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function tone(name) {
    var el = shell() || document.documentElement;
    var value = getComputedStyle(el).getPropertyValue('--anx-tone-' + name).trim();
    return value || css('--color-muted-text');
  }
  function data(id) {
    var node = document.getElementById(id);
    return node ? JSON.parse(node.textContent) : null;
  }
  function touchMode() { return window.matchMedia('(hover: none)').matches; }
  function hint() { return (shell() && shell().dataset.hint) || ''; }
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }
  function go(href) {
    if (!href) return;
    if (window.showLoader) window.showLoader();
    window.location.href = href;
  }

  /* ---------- money on an axis ---------- */
  function trimNumber(n) { return (Math.round(n * 10) / 10).toString().replace(/\.0$/, ''); }
  function compact(value, symbol) {
    var abs = Math.abs(value), sign = value < 0 ? '−' : '';
    if (symbol === '₹' || symbol === 'INR') {
      if (abs >= 1e7) return sign + symbol + trimNumber(abs / 1e7) + 'Cr';
      if (abs >= 1e5) return sign + symbol + trimNumber(abs / 1e5) + 'L';
      if (abs >= 1e3) return sign + symbol + trimNumber(abs / 1e3) + 'k';
      return sign + symbol + Math.round(abs);
    }
    if (abs >= 1e6) return sign + symbol + trimNumber(abs / 1e6) + 'M';
    if (abs >= 1e3) return sign + symbol + trimNumber(abs / 1e3) + 'k';
    return sign + symbol + Math.round(abs);
  }

  /* ---------- the tooltip with a link ---------- */
  function tooltip() {
    if (!tipEl) {
      tipEl = el('div', 'anx-tip');
      tipEl.setAttribute('role', 'dialog');
      document.body.appendChild(tipEl);
    }
    return tipEl;
  }
  function hideTip() { if (tipEl) tipEl.style.display = 'none'; }
  function showTip(x, y, spec, interactive) {
    var tip = tooltip();
    tip.textContent = '';
    tip.appendChild(el('div', 'anx-tip-title', spec.title));
    (spec.lines || []).forEach(function (line) {
      var row = el('div', 'anx-tip-line');
      var left = el('span');
      var dot = el('i');
      dot.style.background = tone(line.c || 'muted');
      left.appendChild(dot);
      left.appendChild(document.createTextNode(line.k));
      row.appendChild(left);
      row.appendChild(el('b', null, line.v));
      tip.appendChild(row);
    });
    if (spec.href) {
      if (interactive) {
        var link = el('a', 'anx-tip-go', spec.cta + ' →');
        link.href = spec.href;
        link.addEventListener('click', function () { if (window.showLoader) window.showLoader(); });
        tip.appendChild(link);
      } else {
        tip.appendChild(el('div', 'anx-tip-hint', hint()));
      }
    }
    tip.style.display = 'block';
    tip.style.pointerEvents = interactive ? 'auto' : 'none';
    var w = tip.offsetWidth, h = tip.offsetHeight;
    var left = x + 12, top = y - h - 12;
    if (left + w > window.innerWidth - 8) left = x - w - 12;
    if (left < 8) left = 8;
    if (top < 8) top = y + 16;
    tip.style.left = left + 'px';
    tip.style.top = top + 'px';
  }
  document.addEventListener('pointerdown', function (e) {
    if (!e.target.closest || !e.target.closest('.anx-tip, canvas, .anx-sk-node, .anx-sk-link')) hideTip();
  });
  window.addEventListener('scroll', hideTip, { passive: true });

  /* ---------- Chart.js, one factory for every chart ---------- */
  function makeChart(canvasId, type, config, tips, extraScales) {
    var canvas = document.getElementById(canvasId);
    if (!canvas || !window.Chart) return null;
    var symbol = shell().dataset.currency || '₹';
    var muted = css('--color-muted-text'), grid = css('--bs-border-color');
    var touch = touchMode();
    var scales = {
      x: { grid: { display: false }, border: { display: false }, ticks: { color: muted, maxRotation: 0, autoSkip: true, maxTicksLimit: 8, font: { size: 11 } } },
      y: { beginAtZero: true, grid: { color: grid }, border: { display: false }, ticks: { color: muted, maxTicksLimit: 6, font: { size: 11 }, callback: function (v) { return compact(v, symbol); } } }
    };
    Object.keys(extraScales || {}).forEach(function (axis) { Object.assign(scales[axis], extraScales[axis]); });
    var chart = new window.Chart(canvas, {
      type: type,
      data: config,
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 250 },
        interaction: { mode: 'index', intersect: false },
        events: touch ? ['click'] : ['mousemove', 'mouseout', 'click'],
        plugins: {
          legend: { display: false },
          tooltip: {
            enabled: false,
            external: function (context) {
              if (touch) return;
              var active = context.tooltip.getActiveElements();
              if (!active.length) { hideTip(); return; }
              var rect = canvas.getBoundingClientRect();
              showTip(rect.left + context.tooltip.caretX, rect.top + context.tooltip.caretY, tips[active[0].index], false);
            }
          }
        },
        onHover: function (e, elements) { e.native.target.style.cursor = elements.length ? 'pointer' : 'default'; },
        onClick: function (e, elements, c) {
          if (touch) {
            var found = c.getElementsAtEventForMode(e, 'index', { intersect: false }, true);
            if (!found.length) { hideTip(); return; }
            var rect = canvas.getBoundingClientRect();
            showTip(rect.left + e.x, rect.top + e.y, tips[found[0].index], true);
            return;
          }
          if (elements.length) go(tips[elements[0].index].href);
        },
        scales: scales
      }
    });
    charts.push(chart);
    return chart;
  }

  function bar(label, values, color, extra) {
    return Object.assign({ label: label, data: values, backgroundColor: color, borderRadius: 4, maxBarThickness: 24 }, extra || {});
  }
  function line(label, values, color, extra) {
    return Object.assign({ label: label, data: values, borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: 0, pointHoverRadius: 5, tension: 0.25, spanGaps: false }, extra || {});
  }

  function drawSpending() {
    var d = data('anx-data-in-out');
    if (d) {
      var sets = [];
      var pair = { barPercentage: 0.94, categoryPercentage: 0.7, maxBarThickness: 44 };
      if (d.income) sets.push(bar('income', d.income, tone('income'), pair));
      sets.push(bar('spending', d.spending, tone('spend'), pair));
      makeChart('anx-in-out', 'bar', { labels: d.labels, datasets: sets }, d.tips);
    }
    var c = data('anx-data-cum');
    if (c) {
      var lines = [line('current', c.current, tone('income'), { fill: true, backgroundColor: tone('income') + '22', borderWidth: 2.5 })];
      if (c.compare) lines.push(line('compare', c.compare, tone('muted'), { borderDash: [5, 4] }));
      if (c.budget) lines.push(line('budget', c.budget, tone('budget'), { borderDash: [2, 4] }));
      makeChart('anx-cum', 'line', { labels: c.labels, datasets: lines }, c.tips);
    }
    var t = data('anx-data-trend');
    if (t && t.series && t.series.length) {
      var palette = ['cat0', 'cat1', 'cat2', 'cat3', 'cat4'];
      var stacks = t.series.map(function (values, i) { return bar(t.names[i], values, tone(palette[i]), { borderRadius: 2 }); });
      makeChart('anx-trend', 'bar', { labels: t.labels, datasets: stacks }, t.tips, { x: { stacked: true }, y: { stacked: true } });
    }
  }

  function drawCashflow() {
    var cmp = data('anx-data-cmp');
    if (cmp) {
      var sets = [line('current', cmp.current, tone('income'), { pointRadius: 3, borderWidth: 2.5 })];
      if (cmp.compare) sets.push(line('compare', cmp.compare, tone('muted'), { borderDash: [5, 4] }));
      makeChart('anx-cmp', 'line', { labels: cmp.labels, datasets: sets }, cmp.tips);
    }
    var sk = data('anx-data-sankey');
    var holder = document.getElementById('anx-sankey');
    if (sk && holder) {
      drawSankey(holder, sk);
      if (window.ResizeObserver) {
        var lastWidth = holder.clientWidth;
        sankeyObserver = new ResizeObserver(function () {
          if (Math.abs(holder.clientWidth - lastWidth) < 4) return;
          lastWidth = holder.clientWidth;
          clearTimeout(sankeyTimer);
          sankeyTimer = setTimeout(function () { drawSankey(holder, sk); }, 120);
        });
        sankeyObserver.observe(holder);
      }
    }
  }

  function drawNetWorth() {
    var s = data('anx-data-nw');
    if (!s) return;
    makeChart('anx-nw', 'line', {
      labels: s.labels,
      datasets: [line('net worth', s.total, tone('income'), { fill: true, backgroundColor: tone('income') + '26', borderWidth: 2.5, pointRadius: 3 })]
    }, s.tips, { y: { beginAtZero: false } });
    var groups = s.groups.map(function (g) { return bar(g.label, g.data, tone(g.key), { borderRadius: 2 }); });
    makeChart('anx-mix', 'bar', { labels: s.labels, datasets: groups }, s.tips, { x: { stacked: true }, y: { stacked: true } });
  }

  /* ---------- the Sankey ---------- */
  function svg(tag, attrs) {
    var node = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    return node;
  }

  function drawSankey(holder, model) {
    holder.textContent = '';
    var width = holder.clientWidth || 600;
    var vertical = width < 640;
    var nodes = model.nodes.map(function (n) { return Object.assign({ out: [], inn: [], outOffset: 0, inOffset: 0 }, n); });
    var byId = {};
    nodes.forEach(function (n) { byId[n.id] = n; });
    var maxLevel = Math.max.apply(null, nodes.map(function (n) { return n.level; }));
    var levels = [];
    for (var l = 0; l <= maxLevel; l++) levels.push(nodes.filter(function (n) { return n.level === l; }));
    var flows = model.links.map(function (f) { return { source: byId[f.source], target: byId[f.target], value: f.value, value_text: f.value_text }; })
      .filter(function (f) { return f.source && f.target; });
    flows.forEach(function (f) { f.source.out.push(f); f.target.inn.push(f); });

    var nodeSize = vertical ? 26 : 14, gap = vertical ? 6 : 10, padTop = 8, padBottom = 8;
    var height, scale = Infinity;
    if (vertical) {
      var padX = 6, rowSpacing = 104;
      height = padTop + maxLevel * rowSpacing + nodeSize + padBottom;
      levels.forEach(function (list) {
        if (!list.length) return;
        var sum = list.reduce(function (a, n) { return a + n.value; }, 0);
        scale = Math.min(scale, (width - 2 * padX - gap * (list.length - 1)) / sum);
      });
      levels.forEach(function (list, i) {
        var total = list.reduce(function (a, n) { return a + n.value * scale; }, 0) + gap * Math.max(0, list.length - 1);
        var x = (width - total) / 2;
        list.forEach(function (n) { n.size = Math.max(2, n.value * scale); n.x = x; n.y = padTop + i * rowSpacing; x += n.size + gap; });
      });
    } else {
      var widest = Math.max.apply(null, levels.map(function (list) { return list.length; }));
      height = Math.max(300, widest * 46 + padTop + padBottom);
      levels.forEach(function (list) {
        if (!list.length) return;
        var sum = list.reduce(function (a, n) { return a + n.value; }, 0);
        scale = Math.min(scale, (height - padTop - padBottom - gap * (list.length - 1)) / sum);
      });
      var padLeft = 124, padRight = maxLevel >= 2 ? 150 : 136;
      var step = maxLevel ? (width - padLeft - padRight - nodeSize) / maxLevel : 0;
      levels.forEach(function (list, i) {
        var total = list.reduce(function (a, n) { return a + n.value * scale; }, 0) + gap * Math.max(0, list.length - 1);
        var y = padTop + (height - padTop - padBottom - total) / 2;
        list.forEach(function (n) { n.size = Math.max(2, n.value * scale); n.x = padLeft + i * step; n.y = y; y += n.size + gap; });
      });
    }

    // lay each flow beside the others at both ends, ordered by where the other end sits, so they cross less
    var pos = vertical ? 'x' : 'y';
    nodes.forEach(function (n) {
      n.out.sort(function (a, b) { return a.target[pos] - b.target[pos]; });
      n.inn.sort(function (a, b) { return a.source[pos] - b.source[pos]; });
    });
    flows.forEach(function (f) { f.width = Math.max(1, f.value * scale); });
    nodes.forEach(function (n) {
      var o = 0; n.out.forEach(function (f) { f.sourceOffset = o + f.width / 2; o += f.width; });
      var i = 0; n.inn.forEach(function (f) { f.targetOffset = i + f.width / 2; i += f.width; });
    });

    var root = svg('svg', { viewBox: '0 0 ' + width + ' ' + height, width: width, height: height, 'aria-hidden': 'true' });
    var linkLayer = svg('g'), nodeLayer = svg('g');
    root.appendChild(linkLayer);
    root.appendChild(nodeLayer);
    var touch = touchMode();

    function bindTip(target, spec, x, y) {
      var open = function (clientX, clientY, interactive) { showTip(clientX, clientY, spec, interactive); };
      target.setAttribute('tabindex', '0');
      target.setAttribute('role', 'link');
      target.setAttribute('aria-label', spec.title + ', ' + spec.lines.map(function (l) { return l.v; }).join(', '));
      if (!touch) {
        target.addEventListener('mousemove', function (e) { open(e.clientX, e.clientY, false); });
        target.addEventListener('mouseleave', hideTip);
        target.addEventListener('click', function () { go(spec.href); });
      } else {
        target.addEventListener('click', function (e) { e.preventDefault(); open(e.clientX, e.clientY, true); });
      }
      target.addEventListener('keydown', function (e) { if (e.key === 'Enter') go(spec.href); });
    }

    flows.forEach(function (f) {
      var d;
      if (vertical) {
        var x0 = f.source.x + f.sourceOffset, y0 = f.source.y + nodeSize, x1 = f.target.x + f.targetOffset, y1 = f.target.y, ym = (y0 + y1) / 2;
        d = 'M' + x0 + ',' + y0 + ' C' + x0 + ',' + ym + ' ' + x1 + ',' + ym + ' ' + x1 + ',' + y1;
      } else {
        var sx = f.source.x + nodeSize, sy = f.source.y + f.sourceOffset, tx = f.target.x, ty = f.target.y + f.targetOffset, xm = (sx + tx) / 2;
        d = 'M' + sx + ',' + sy + ' C' + xm + ',' + sy + ' ' + xm + ',' + ty + ' ' + tx + ',' + ty;
      }
      var path = svg('path', { d: d, 'class': 'anx-sk-link', 'stroke-width': f.width, stroke: tone(f.target.tone) });
      bindTip(path, {
        title: f.source.label + ' → ' + f.target.label,
        lines: [{ k: model.labels.amount, v: f.value_text, c: f.target.tone }],
        href: f.target.href, cta: model.labels.cta
      });
      linkLayer.appendChild(path);
    });

    nodes.forEach(function (n) {
      var group = svg('g', { 'class': 'anx-sk-node' });
      var rect = vertical
        ? svg('rect', { x: n.x, y: n.y, width: n.size, height: nodeSize, rx: 4, fill: tone(n.tone) })
        : svg('rect', { x: n.x, y: n.y, width: nodeSize, height: n.size, rx: 3, fill: tone(n.tone) });
      group.appendChild(rect);
      var lines = [{ k: model.labels.amount, v: n.value_text, c: n.tone }];
      if (n.share_text) lines.push({ k: model.labels.share, v: n.share_text, c: 'muted' });
      if (n.delta && n.delta.text) lines.push({ k: model.labels.change, v: n.delta.text, c: 'muted' });
      bindTip(group, { title: n.label, lines: lines, href: n.href, cta: model.labels.cta });

      if (vertical) {
        if (n.size >= 64) {
          var inside = svg('text', { x: n.x + n.size / 2, y: n.y + nodeSize / 2 + 4, 'text-anchor': 'middle', 'class': 'anx-sk-inside' });
          inside.textContent = shorten(n.label, Math.floor(n.size / 6.4));
          group.appendChild(inside);
        }
      } else if (n.size >= 6) {
        var left = n.level === 0 && maxLevel > 0;
        var tx = left ? n.x - 8 : n.x + nodeSize + 8;
        var label = svg('text', { x: tx, y: n.y + n.size / 2 - (n.size >= 24 ? 2 : -4), 'text-anchor': left ? 'end' : 'start' });
        label.textContent = shorten(n.label, left ? 20 : 24);
        group.appendChild(label);
        if (n.size >= 24) {
          var sub = svg('text', { x: tx, y: n.y + n.size / 2 + 12, 'text-anchor': left ? 'end' : 'start', 'class': 'anx-sk-sub' });
          sub.textContent = n.value_text + (n.share_text ? ' · ' + n.share_text : '');
          group.appendChild(sub);
        }
      }
      nodeLayer.appendChild(group);
    });
    holder.appendChild(root);
  }

  function shorten(text, max) { return text.length > max ? text.slice(0, Math.max(1, max - 1)) + '…' : text; }

  /* ---------- lifecycle ---------- */
  function destroy() {
    charts.forEach(function (c) { c.destroy(); });
    charts = [];
    if (sankeyObserver) { sankeyObserver.disconnect(); sankeyObserver = null; }
    clearTimeout(sankeyTimer);
    hideTip();
  }

  function init() {
    var root = shell();
    destroy();
    if (!root) return;
    var tab = root.dataset.tab;
    if (tab === 'spend') drawSpending();
    else if (tab === 'cashflow') drawCashflow();
    else if (tab === 'networth') drawNetWorth();
  }

  document.addEventListener('DOMContentLoaded', function () {
    init();
    if (!themeObserver) {
      themeObserver = new MutationObserver(function () { init(); });
      themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-bs-theme'] });
    }
  });
  document.body.addEventListener('htmx:beforeSwap', function (e) {
    if (e.detail && e.detail.target && e.detail.target.id === 'analytics-shell') destroy();
  });
  document.body.addEventListener('htmx:afterSettle', function (e) {
    if (e.target && e.target.id === 'analytics-shell') init();
  });
})();
