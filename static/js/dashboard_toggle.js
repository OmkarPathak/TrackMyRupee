/**
 * Dashboard Mode Toggle Logic
 * Handles switching between Daily Mode and Full Report.
 * Context-aware: Performs local toggle if on dashboard, otherwise redirects.
 */

function switchToDailyMode() {
    const dailyView = document.getElementById('daily-mode-view');
    const fullView = document.getElementById('full-report-view');

    if (dailyView && fullView) {
        // Local Toggle
        dailyView.style.display = 'block';
        fullView.style.display = 'none';
        
        // Update all daily mode buttons (navbar + content)
        document.querySelectorAll('.daily-mode-btn').forEach(btn => btn.classList.add('active'));
        document.querySelectorAll('.full-report-btn').forEach(btn => btn.classList.remove('active'));
        
        localStorage.setItem('tmr_dashboard_mode', 'daily');
    } else {
        // Redirect to Home with mode
        localStorage.setItem('tmr_dashboard_mode', 'daily');
        window.location.href = window.homeUrl || '/';
    }
}

function switchToFullReport() {
    const dailyView = document.getElementById('daily-mode-view');
    const fullView = document.getElementById('full-report-view');

    if (dailyView && fullView) {
        // Local Toggle
        dailyView.style.display = 'none';
        fullView.style.display = 'block';
        
        // Update all full report buttons (navbar + content)
        document.querySelectorAll('.daily-mode-btn').forEach(btn => btn.classList.remove('active'));
        document.querySelectorAll('.full-report-btn').forEach(btn => btn.classList.add('active'));
        
        localStorage.setItem('tmr_dashboard_mode', 'full');
        
        // Trigger Chart.js resize since canvases are now visible
        if (window.dispatchEvent) {
            window.dispatchEvent(new Event('resize'));
        }
        
        // Explicitly update moneyFlowChart if it exists
        if (window.charts && window.charts.moneyFlow) {
            window.charts.moneyFlow.resize();
            window.charts.moneyFlow.update();
        }
    } else {
        // Redirect to Home with mode
        localStorage.setItem('tmr_dashboard_mode', 'full');
        window.location.href = window.homeUrl || '/';
    }
}

// Restore last-used mode on page load (only on Home page)
document.addEventListener('DOMContentLoaded', function() {
    const dailyView = document.getElementById('daily-mode-view');
    const fullView = document.getElementById('full-report-view');
    
    if (dailyView && fullView) {
        const urlParams = new URLSearchParams(window.location.search);
        const urlMode = urlParams.get('mode');
        const savedMode = localStorage.getItem('tmr_dashboard_mode');
        
        if (urlMode === 'full' || (urlMode === null && savedMode === 'full')) {
            switchToFullReport();
        } else {
            switchToDailyMode();
        }
    }
});
