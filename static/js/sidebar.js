/**
 * Sidebar Toggle and State Management
 */
document.addEventListener('DOMContentLoaded', function() {
    const sidebar = document.getElementById('sidebar');
    const sidebarCollapseBtn = document.getElementById('sidebarCollapse');
    const body = document.body;

    if (!sidebar || !sidebarCollapseBtn) return;

    // Check localStorage for saved sidebar state
    const sidebarState = localStorage.getItem('sidebarState');
    
    if (sidebarState === 'collapsed') {
        sidebar.classList.add('collapsed');
        body.classList.add('sidebar-collapsed');
        const icon = sidebarCollapseBtn.querySelector('i');
        if (icon) {
            icon.classList.remove('bi-chevron-left');
            icon.classList.add('bi-chevron-right');
        }
    }

    sidebarCollapseBtn.addEventListener('click', function() {
        sidebar.classList.toggle('collapsed');
        body.classList.toggle('sidebar-collapsed');

        const icon = sidebarCollapseBtn.querySelector('i');
        if (sidebar.classList.contains('collapsed')) {
            localStorage.setItem('sidebarState', 'collapsed');
            if (icon) {
                icon.classList.remove('bi-chevron-left');
                icon.classList.add('bi-chevron-right');
            }
        } else {
            localStorage.setItem('sidebarState', 'expanded');
            if (icon) {
                icon.classList.remove('bi-chevron-right');
                icon.classList.add('bi-chevron-left');
            }
        }
    });

    // Handle hover effects for toggle button visibility in collapsed mode
    sidebar.addEventListener('mouseenter', function() {
        if (sidebar.classList.contains('collapsed')) {
            // Optional: Show some indicators or peak
        }
    });
});
