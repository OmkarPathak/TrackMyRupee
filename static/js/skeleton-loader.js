/* ============================================================
   Skeleton Loader — JS Module  
   URL-pattern matching + page-specific skeleton HTML templates
   ============================================================ */

(function () {
  'use strict';

  // ── URL → Skeleton Type Mapping ──────────────────────────

  const ROUTE_MAP = [
    // Dashboard
    { pattern: /^\/dashboard\/?$/,                 type: 'dashboard' },

    // Analytics
    { pattern: /^\/analytics\/?$/,                 type: 'analytics' },
    { pattern: /^\/analytics\/mom\/?$/,            type: 'analytics' },
    { pattern: /^\/year-in-review/,                type: 'analytics' },

    // Calendar
    { pattern: /^\/calendar/,                      type: 'calendar' },

    // Detail pages (must come before list patterns)
    { pattern: /^\/accounts\/\d+\/?$/,             type: 'detail' },
    { pattern: /^\/goals\/\d+\/?$/,                type: 'detail' },

    // Form pages (create / edit)
    { pattern: /^\/expenses\/add/,                 type: 'form' },
    { pattern: /^\/expenses\/\d+\/edit/,           type: 'form' },
    { pattern: /^\/income\/add/,                   type: 'form' },
    { pattern: /^\/income\/\d+\/edit/,             type: 'form' },
    { pattern: /^\/accounts\/add/,                 type: 'form' },
    { pattern: /^\/accounts\/\d+\/edit/,           type: 'form' },
    { pattern: /^\/category\/add/,                 type: 'form' },
    { pattern: /^\/category\/\d+\/edit/,           type: 'form' },
    { pattern: /^\/transfers\/add/,                type: 'form' },
    { pattern: /^\/transfers\/\d+\/edit/,          type: 'form' },
    { pattern: /^\/recurring\/create/,             type: 'form' },
    { pattern: /^\/recurring\/\d+\/edit/,          type: 'form' },
    { pattern: /^\/goals\/add/,                    type: 'form' },
    { pattern: /^\/goals\/\d+\/edit/,              type: 'form' },
    { pattern: /^\/goals\/contribution/,           type: 'form' },
    { pattern: /^\/settings\/currency/,            type: 'form' },
    { pattern: /^\/settings\/language/,            type: 'form' },
    { pattern: /^\/settings\/profile/,             type: 'form' },
    { pattern: /^\/contact/,                       type: 'form' },
    { pattern: /^\/onboarding/,                    type: 'form' },
    { pattern: /^\/upload/,                        type: 'form' },
    { pattern: /^\/account\/delete/,               type: 'form' },

    // Card grid pages
    { pattern: /^\/goals\/?$/,                     type: 'card-grid' },
    { pattern: /^\/recurring\/?$/,                 type: 'card-grid' },
    { pattern: /^\/pricing/,                       type: 'card-grid' },

    // Settings-layout pages (extends settings_base.html)
    { pattern: /^\/budget\/?$/,                    type: 'settings' },
    { pattern: /^\/income\/list/,                  type: 'settings' },
    { pattern: /^\/category\/list/,                type: 'settings' },
    { pattern: /^\/accounts\/list/,                type: 'settings' },
    { pattern: /^\/transfers\/?$/,                 type: 'settings' },
    { pattern: /^\/settings/,                      type: 'settings' },

    // Table list pages
    { pattern: /^\/expenses\/?$/,                  type: 'table-list' },
    { pattern: /^\/notifications/,                 type: 'table-list' },
    { pattern: /^\/recurring\/manage/,             type: 'table-list' },
    { pattern: /^\/export/,                        type: 'table-list' },
  ];

  function getSkeletonType(url) {
    try {
      var pathname = new URL(url, window.location.origin).pathname;
      for (var i = 0; i < ROUTE_MAP.length; i++) {
        if (ROUTE_MAP[i].pattern.test(pathname)) {
          return ROUTE_MAP[i].type;
        }
      }
    } catch (e) { /* ignore */ }
    return 'generic';
  }

  // ── Skeleton HTML Builders ───────────────────────────────

  function b(classes) {
    return '<div class="skeleton-block ' + classes + '"></div>';
  }

  function buildDashboard() {
    return '' +
      // Toggle bar
      '<div class="sk-toggle-bar">' +
        '<div class="sk-toggle-inner">' +
          b('sk-h-38 sk-rounded-pill" style="width:120px') + 
          b('sk-h-38 sk-rounded-pill" style="width:120px') + 
          b('sk-h-38 sk-rounded-pill" style="width:120px') + 
        '</div>' +
      '</div>' +
      // 4 stat cards
      '<div class="sk-grid-4 mb-4">' +
        buildStatCard() + buildStatCard() + buildStatCard() + buildStatCard() +
      '</div>' +
      // Large chart
      '<div class="sk-chart mb-4">' +
        '<div class="sk-chart-header">' +
          b('sk-h-14 sk-w-30') +
          b('sk-h-12 sk-rounded-pill" style="width:60px') +
        '</div>' +
        b('sk-h-250 sk-w-100') +
      '</div>' +
      // 2 smaller charts
      '<div class="sk-grid-2">' +
        '<div class="sk-chart">' +
          '<div class="sk-chart-header">' + b('sk-h-14 sk-w-40') + '</div>' +
          b('sk-h-200 sk-w-100') +
        '</div>' +
        '<div class="sk-chart">' +
          '<div class="sk-chart-header">' + b('sk-h-14 sk-w-40') + '</div>' +
          b('sk-h-200 sk-w-100') +
        '</div>' +
      '</div>';
  }

  function buildStatCard() {
    return '<div class="sk-card">' +
      b('sk-h-10 sk-w-60" style="margin-bottom:10px') +
      b('sk-h-24 sk-w-50') +
    '</div>';
  }

  function buildTableList() {
    return '' +
      // Header
      '<div class="sk-header">' +
        '<div class="sk-header-left">' +
          b('sk-h-24" style="width:200px') +
          b('sk-h-10" style="width:280px') +
        '</div>' +
        b('sk-h-32 sk-rounded-pill" style="width:120px') +
      '</div>' +
      // Filter bar
      '<div class="sk-filter-bar">' +
        b('sk-h-32" style="width:130px') +
        b('sk-h-32" style="width:130px') +
        b('sk-h-32" style="width:130px') +
        b('sk-h-32 sk-rounded-pill" style="width:80px') +
      '</div>' +
      // Stats
      '<div class="sk-stats-row">' +
        '<div class="sk-stat-box">' + b('sk-h-8 sk-w-60') + b('sk-h-16 sk-w-40') + '</div>' +
        '<div class="sk-stat-box">' + b('sk-h-8 sk-w-60') + b('sk-h-16 sk-w-50') + '</div>' +
      '</div>' +
      // Table
      buildTable(5, 5);
  }

  function buildTable(cols, rows) {
    var html = '<div class="sk-table">';
    // Header
    html += '<div class="sk-table-header">';
    for (var c = 0; c < cols; c++) {
      var w = c === 0 ? '15%' : (c === cols - 1 ? '10%' : Math.floor(65 / (cols - 2)) + '%');
      html += b('sk-h-12" style="flex:0 0 ' + w);
    }
    html += '</div>';
    // Rows
    for (var r = 0; r < rows; r++) {
      html += '<div class="sk-table-row">';
      for (var c2 = 0; c2 < cols; c2++) {
        var w2 = c2 === 0 ? '15%' : (c2 === cols - 1 ? '10%' : Math.floor(65 / (cols - 2)) + '%');
        var rw = (70 + Math.floor(Math.random() * 30));
        html += '<div style="flex:0 0 ' + w2 + '">' + b('sk-h-12" style="width:' + rw + '%') + '</div>';
      }
      html += '</div>';
    }
    html += '</div>';
    return html;
  }

  function buildCardGrid() {
    return '' +
      // Header
      '<div class="sk-header">' +
        '<div class="sk-header-left">' +
          b('sk-h-24" style="width:180px') +
          b('sk-h-10" style="width:320px') +
        '</div>' +
        b('sk-h-32 sk-rounded-pill" style="width:100px') +
      '</div>' +
      // Summary card
      '<div class="sk-card mb-4">' +
        '<div style="display:flex;justify-content:space-between;align-items:center">' +
          '<div>' + b('sk-h-10 sk-w-60" style="width:140px;margin-bottom:6px') + b('sk-h-24" style="width:180px') + '</div>' +
          '<div>' + b('sk-h-10 sk-w-60" style="width:140px;margin-bottom:6px') + b('sk-h-20" style="width:160px') + '</div>' +
        '</div>' +
      '</div>' +
      // 3 cards
      '<div class="sk-grid-3">' +
        buildContentCard() + buildContentCard() + buildContentCard() +
      '</div>';
  }

  function buildContentCard() {
    return '<div class="sk-card">' +
      '<div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">' +
        b('sk-h-40 sk-rounded-circle" style="width:40px;flex-shrink:0') +
        '<div style="flex:1">' + b('sk-h-14 sk-w-60" style="margin-bottom:6px') + b('sk-h-10 sk-w-80') + '</div>' +
      '</div>' +
      b('sk-h-8 sk-w-100" style="margin-bottom:12px;border-radius:8px') +
      '<div style="display:flex;justify-content:space-between;margin-top:12px">' +
        b('sk-h-32 sk-rounded-pill" style="width:90px') +
        '<div style="display:flex;gap:8px">' +
          b('sk-h-32" style="width:32px') +
          b('sk-h-32" style="width:32px') +
        '</div>' +
      '</div>' +
    '</div>';
  }

  function buildAnalytics() {
    return '' +
      // Header
      '<div class="sk-header">' +
        '<div class="sk-header-left">' +
          b('sk-h-24" style="width:220px') +
          b('sk-h-10" style="width:340px') +
        '</div>' +
        '<div style="display:flex;gap:8px">' +
          b('sk-h-32 sk-rounded-pill" style="width:100px') +
          b('sk-h-32" style="width:100px') +
        '</div>' +
      '</div>' +
      // Section 1
      '<div style="display:flex;align-items:center;gap:8px;margin-bottom:16px">' +
        b('sk-h-32 sk-rounded-circle" style="width:32px;flex-shrink:0') +
        b('sk-h-16" style="width:160px') +
      '</div>' +
      b('sk-h-10 sk-w-70" style="margin-bottom:20px') +
      // Health card
      '<div class="sk-card mb-4">' +
        '<div style="display:flex;align-items:center;gap:24px;flex-wrap:wrap">' +
          b('sk-h-120 sk-rounded-circle" style="width:120px;flex-shrink:0') +
          '<div style="flex:1;min-width:200px">' +
            b('sk-h-20 sk-w-40" style="margin-bottom:8px') +
            b('sk-h-12 sk-w-80" style="margin-bottom:12px') +
            b('sk-h-8 sk-w-100" style="border-radius:8px') +
          '</div>' +
        '</div>' +
      '</div>' +
      // 2 charts
      '<div class="sk-grid-2 mb-4">' +
        '<div class="sk-chart">' +
          '<div class="sk-chart-header">' + b('sk-h-14 sk-w-40') + '</div>' +
          b('sk-h-250 sk-w-100') +
        '</div>' +
        '<div class="sk-chart">' +
          '<div class="sk-chart-header">' + b('sk-h-14 sk-w-40') + '</div>' +
          b('sk-h-250 sk-w-100') +
        '</div>' +
      '</div>' +
      // Section 2
      '<div style="display:flex;align-items:center;gap:8px;margin-bottom:16px;margin-top:24px">' +
        b('sk-h-32 sk-rounded-circle" style="width:32px;flex-shrink:0') +
        b('sk-h-16" style="width:180px') +
      '</div>' +
      // Wide chart
      '<div class="sk-chart">' +
        '<div class="sk-chart-header">' + b('sk-h-14 sk-w-30') + '</div>' +
        b('sk-h-250 sk-w-100') +
      '</div>';
  }

  function buildDetail() {
    return '' +
      // Breadcrumb
      '<div class="sk-breadcrumb">' +
        b('sk-h-10" style="width:60px') +
        '<span class="sk-breadcrumb-sep">›</span>' +
        b('sk-h-10" style="width:60px') +
        '<span class="sk-breadcrumb-sep">›</span>' +
        b('sk-h-10" style="width:80px') +
      '</div>' +
      // Header
      '<div class="sk-header" style="margin-bottom:1.5rem">' +
        '<div class="sk-header-left">' +
          b('sk-h-24" style="width:200px') +
          b('sk-h-12" style="width:160px') +
        '</div>' +
        '<div style="display:flex;gap:8px">' +
          b('sk-h-38 sk-rounded-pill" style="width:110px') +
          b('sk-h-38 sk-rounded-pill" style="width:110px') +
        '</div>' +
      '</div>' +
      // Stat cards row
      '<div style="display:flex;gap:1rem;margin-bottom:1.5rem;flex-wrap:wrap">' +
        '<div class="sk-card" style="flex:1;min-width:150px">' +
          b('sk-h-10 sk-w-60" style="margin-bottom:8px') +
          b('sk-h-24 sk-w-50') +
        '</div>' +
        '<div class="sk-card" style="flex:2;min-width:250px">' +
          b('sk-h-10 sk-w-30" style="margin-bottom:8px') +
          b('sk-h-120 sk-w-100') +
        '</div>' +
      '</div>' +
      // Table
      buildTable(5, 6);
  }

  function buildForm() {
    return '' +
      '<div style="max-width:580px;margin:0 auto">' +
        // Header
        '<div class="sk-header" style="margin-bottom:2rem">' +
          b('sk-h-20" style="width:160px') +
          b('sk-h-32 sk-rounded-pill" style="width:130px') +
        '</div>' +
        // Two-col row
        '<div style="display:flex;gap:1rem;margin-bottom:1.25rem">' +
          '<div style="flex:5">' +
            '<div class="sk-form-group">' +
              b('sk-h-12 sk-w-30 sk-label') +
              b('sk-h-40 sk-w-100') +
            '</div>' +
          '</div>' +
          '<div style="flex:7">' +
            '<div class="sk-form-group">' +
              b('sk-h-12 sk-w-30 sk-label') +
              b('sk-h-40 sk-w-100') +
            '</div>' +
          '</div>' +
        '</div>' +
        // Single fields
        '<div class="sk-form-group">' +
          b('sk-h-12 sk-w-25 sk-label') +
          b('sk-h-40 sk-w-100') +
        '</div>' +
        '<div class="sk-form-group">' +
          b('sk-h-12 sk-w-20 sk-label') +
          b('sk-h-40 sk-w-100') +
        '</div>' +
        // More options link
        b('sk-h-12" style="width:110px;margin-bottom:1.5rem') +
        // Action buttons
        '<div style="display:flex;justify-content:flex-end;gap:12px;margin-top:2rem">' +
          b('sk-h-38 sk-rounded-pill" style="width:100px') +
          b('sk-h-38 sk-rounded-pill" style="width:100px') +
        '</div>' +
      '</div>';
  }

  function buildSettings() {
    return '' +
      '<div class="sk-settings-layout">' +
        // Sidebar (hidden on mobile via CSS)
        '<div class="sk-sidebar">' +
          // Plan card
          '<div class="sk-card" style="margin-bottom:12px;padding:14px">' +
            b('sk-h-8" style="width:70px;margin-bottom:6px') +
            b('sk-h-16" style="width:100px') +
          '</div>' +
          b('sk-h-8" style="width:50px;margin-bottom:10px') +
          buildSidebarItems(7) +
          '<div style="margin:12px 0"></div>' +
          b('sk-h-8" style="width:50px;margin-bottom:10px') +
          buildSidebarItems(4) +
        '</div>' +
        // Content
        '<div class="sk-settings-content">' +
          '<div class="sk-header">' +
            '<div class="sk-header-left">' +
              b('sk-h-24" style="width:160px') +
              b('sk-h-10" style="width:280px') +
            '</div>' +
            b('sk-h-32 sk-rounded-pill" style="width:110px') +
          '</div>' +
          buildTable(4, 5) +
        '</div>' +
      '</div>';
  }

  function buildSidebarItems(count) {
    var html = '';
    for (var i = 0; i < count; i++) {
      var w = (50 + Math.floor(Math.random() * 40)) + '%';
      html += '<div class="sk-sidebar-item">' + b('sk-h-12" style="width:' + w) + '</div>';
    }
    return html;
  }

  function buildCalendar() {
    return '' +
      // Header
      '<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:1rem">' +
        '<div style="display:flex;align-items:center;gap:12px">' +
          b('sk-h-32" style="width:32px;border-radius:8px') +
          b('sk-h-24" style="width:200px') +
          b('sk-h-32" style="width:32px;border-radius:8px') +
        '</div>' +
        '<div style="display:flex;gap:8px">' +
          b('sk-h-32" style="width:180px;border-radius:8px') +
          b('sk-h-32 sk-rounded-pill" style="width:130px') +
        '</div>' +
      '</div>' +
      // Legend bar
      '<div style="display:flex;justify-content:space-between;align-items:center;padding:12px 0;border-bottom:1px solid rgba(0,0,0,0.05);margin-bottom:0">' +
        b('sk-h-16" style="width:250px') +
        '<div style="display:flex;gap:16px">' +
          b('sk-h-10" style="width:60px') +
          b('sk-h-10" style="width:60px') +
          b('sk-h-10" style="width:70px') +
        '</div>' +
      '</div>' +
      // Weekday headers
      '<div class="sk-calendar-weekdays">' +
        buildCalendarWeekdays() +
      '</div>' +
      // Calendar grid (5 rows × 7 cols)
      buildCalendarRows(5);
  }

  function buildCalendarWeekdays() {
    var days = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];
    var html = '';
    for (var i = 0; i < 7; i++) {
      html += '<div>' + b('sk-h-10" style="width:24px') + '</div>';
    }
    return html;
  }

  function buildCalendarRows(rows) {
    var html = '';
    for (var r = 0; r < rows; r++) {
      html += '<div class="sk-calendar-grid">';
      for (var c = 0; c < 7; c++) {
        var isEmpty = (r === 0 && c < 2) || (r === rows - 1 && c > 4);
        if (isEmpty) {
          html += '<div class="sk-calendar-cell" style="opacity:0.3"></div>';
        } else {
          html += '<div class="sk-calendar-cell">' +
            b('sk-h-10" style="width:18px;margin-bottom:8px') +
            (Math.random() > 0.4 ? b('sk-h-10 sk-w-70" style="margin-bottom:4px') : '') +
            (Math.random() > 0.6 ? b('sk-h-10 sk-w-50') : '') +
          '</div>';
        }
      }
      html += '</div>';
    }
    return html;
  }

  function buildGeneric() {
    return '' +
      '<div class="sk-header">' +
        '<div class="sk-header-left">' +
          b('sk-h-24" style="width:200px') +
          b('sk-h-10" style="width:300px') +
        '</div>' +
        b('sk-h-32 sk-rounded-pill" style="width:100px') +
      '</div>' +
      '<div class="sk-stats-row">' +
        '<div class="sk-stat-box">' + b('sk-h-8 sk-w-60') + b('sk-h-16 sk-w-40') + '</div>' +
        '<div class="sk-stat-box">' + b('sk-h-8 sk-w-70') + b('sk-h-16 sk-w-50') + '</div>' +
      '</div>' +
      '<div class="sk-card">' +
        b('sk-h-14 sk-w-30" style="margin-bottom:16px') +
        b('sk-h-12 sk-w-80" style="margin-bottom:12px') +
        b('sk-h-12 sk-w-60" style="margin-bottom:12px') +
        b('sk-h-12 sk-w-70" style="margin-bottom:12px') +
        b('sk-h-12 sk-w-50') +
      '</div>';
  }

  // ── Builder Registry ─────────────────────────────────────

  var BUILDERS = {
    'dashboard':   buildDashboard,
    'table-list':  buildTableList,
    'card-grid':   buildCardGrid,
    'analytics':   buildAnalytics,
    'detail':      buildDetail,
    'form':        buildForm,
    'settings':    buildSettings,
    'calendar':    buildCalendar,
    'generic':     buildGeneric
  };

  // ── Show / Hide API ──────────────────────────────────────

  var overlayEl = null;

  window.showSkeletonLoader = function (destinationUrl) {
    hideSkeletonInternal();

    var type = destinationUrl ? getSkeletonType(destinationUrl) : 'generic';
    var builder = BUILDERS[type] || BUILDERS['generic'];

    // 1. Static Progress Bar (handled by base.html for speed, but we can nudge it here)
    var bar = document.getElementById('global-progress-bar');
    if (bar) {
        bar.classList.add('active');
    }

    // 2. Create Skeleton Overlay
    overlayEl = document.createElement('div');
    overlayEl.className = 'skeleton-overlay';
    overlayEl.id = 'skeleton-loader-overlay';
    overlayEl.innerHTML = '<div class="skeleton-inner">' + builder() + '</div>';
    document.body.appendChild(overlayEl);

    // Prevent body scroll while skeleton is visible
    document.body.style.overflow = 'hidden';
  };

  window.hideSkeletonLoader = function () {
    hideSkeletonInternal();
  };

  function hideSkeletonInternal() {
    // 1. Remove Overlay
    if (overlayEl && overlayEl.parentNode) {
      overlayEl.parentNode.removeChild(overlayEl);
    }
    overlayEl = null;

    // 2. Reset Static Progress Bar
    var bar = document.getElementById('global-progress-bar');
    if (bar) {
        bar.classList.remove('active');
        bar.style.width = '0';
    }

    document.body.style.overflow = '';
    
    // Safety cleanup
    var orphans = document.querySelectorAll('#skeleton-loader-overlay');
    for (var i = 0; i < orphans.length; i++) {
        if (orphans[i].parentNode) {
            orphans[i].parentNode.removeChild(orphans[i]);
        }
    }
  }

  // Auto-hide when page loads (handles bfcache, normal navigation)
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', hideSkeletonInternal);
  }

})();
