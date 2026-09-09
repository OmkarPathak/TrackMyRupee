/**
 * Sidebar Toggle, Keyboard Shortcut & Page Search Management
 */
(function() {
    function initSidebar() {
        const sidebar = document.getElementById('sidebar');
        const sidebarCollapseBtn = document.getElementById('sidebarCollapse');
        const body = document.body;

        if (sidebar && sidebarCollapseBtn) {
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

            // Remove existing listener to avoid duplicates
            sidebarCollapseBtn.onclick = function() {
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
            };
        }

        // Manage & preserve sidebar scroll position across page loads / navigation
        const sidebarContent = sidebar ? sidebar.querySelector('.sidebar-content') : null;
        if (sidebarContent) {
            // Restore saved scroll position if available in sessionStorage
            const savedScrollTop = sessionStorage.getItem('sidebarScrollTop');
            if (savedScrollTop !== null) {
                sidebarContent.scrollTop = parseInt(savedScrollTop, 10);
            }

            // Always ensure active link is visible in sidebar scroll container
            const activeLink = sidebarContent.querySelector('.nav-link.active');
            if (activeLink) {
                const containerRect = sidebarContent.getBoundingClientRect();
                const activeRect = activeLink.getBoundingClientRect();

                const isAbove = activeRect.top < containerRect.top;
                const isBelow = activeRect.bottom > containerRect.bottom;

                if (isAbove || isBelow) {
                    activeLink.scrollIntoView({ block: 'nearest', behavior: 'instant' });
                }
            }

            // Save scroll position on scroll and link click
            if (!sidebarContent.dataset.scrollListenerAttached) {
                sidebarContent.dataset.scrollListenerAttached = 'true';
                
                let scrollTimeout;
                sidebarContent.addEventListener('scroll', function() {
                    if (scrollTimeout) clearTimeout(scrollTimeout);
                    scrollTimeout = setTimeout(function() {
                        sessionStorage.setItem('sidebarScrollTop', sidebarContent.scrollTop);
                    }, 50);
                }, { passive: true });

                sidebarContent.addEventListener('click', function(e) {
                    const link = e.target.closest('.nav-link');
                    if (link) {
                        sessionStorage.setItem('sidebarScrollTop', sidebarContent.scrollTop);
                    }
                });
            }
        }

        // Detect OS for keyboard shortcut display (⌘ K vs Ctrl K)
        const commandKbd = document.querySelectorAll('.command-kbd');
        const isMac = (navigator.userAgentData && navigator.userAgentData.platform)
            ? navigator.userAgentData.platform.toUpperCase().indexOf('MAC') >= 0
            : (navigator.platform || navigator.userAgent).toUpperCase().indexOf('MAC') >= 0;

        if (commandKbd.length > 0) {
            commandKbd.forEach(kbd => {
                kbd.textContent = isMac ? '⌘ K' : 'Ctrl K';
            });
        }
    }

    // Global Command Palette Modal Trigger Function
    window.togglePageSearchModal = function() {
        const searchModalEl = document.getElementById('pageSearchModal');
        if (searchModalEl && typeof bootstrap !== 'undefined') {
            const modal = bootstrap.Modal.getOrCreateInstance(searchModalEl);
            modal.toggle();
        }
    };

    // Global Keydown Listener for ⌘ K / Ctrl K (Always active)
    document.addEventListener('keydown', function(e) {
        if ((e.metaKey || e.ctrlKey) && (e.key.toLowerCase() === 'k' || e.code === 'KeyK')) {
            e.preventDefault();
            e.stopPropagation();
            window.togglePageSearchModal();
        }
    }, true);

    // Page Search Live Filter Logic (Using event delegation on document)
    let selectedIndex = -1;

    function getSearchItems() {
        return Array.from(document.querySelectorAll('#pageSearchList .search-page-item, #pageSearchSettingsList .search-page-item'));
    }

    function filterPages(query) {
        const searchItems = getSearchItems();
        const noResultsEl = document.getElementById('noPageSearchMatches');
        let matchCount = 0;

        searchItems.forEach(item => {
            const text = (item.textContent || '').toLowerCase();
            if (!query || text.includes(query)) {
                item.classList.remove('d-none');
                matchCount++;
            } else {
                item.classList.add('d-none');
            }
            item.classList.remove('active', 'bg-primary-subtle');
        });

        if (noResultsEl) {
            if (matchCount === 0) {
                noResultsEl.classList.remove('d-none');
            } else {
                noResultsEl.classList.add('d-none');
            }
        }
    }

    function highlightItem(items, index) {
        items.forEach((item, i) => {
            if (i === index) {
                item.classList.add('bg-primary-subtle');
                item.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            } else {
                item.classList.remove('bg-primary-subtle');
            }
        });
    }

    // Attach search modal events
    document.addEventListener('shown.bs.modal', function(e) {
        if (e.target && e.target.id === 'pageSearchModal') {
            const input = document.getElementById('pageSearchInput');
            if (input) {
                input.value = '';
                selectedIndex = -1;
                filterPages('');
                setTimeout(() => input.focus(), 50);
            }
        }
    });

    document.addEventListener('input', function(e) {
        if (e.target && e.target.id === 'pageSearchInput') {
            selectedIndex = -1;
            filterPages(e.target.value.trim().toLowerCase());
        }
    });

    document.addEventListener('keydown', function(e) {
        if (e.target && e.target.id === 'pageSearchInput') {
            const visibleItems = getSearchItems().filter(item => !item.classList.contains('d-none'));
            if (visibleItems.length === 0) return;

            if (e.key === 'ArrowDown') {
                e.preventDefault();
                selectedIndex = (selectedIndex + 1) % visibleItems.length;
                highlightItem(visibleItems, selectedIndex);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                selectedIndex = (selectedIndex - 1 + visibleItems.length) % visibleItems.length;
                highlightItem(visibleItems, selectedIndex);
            } else if (e.key === 'Enter') {
                e.preventDefault();
                if (selectedIndex >= 0 && selectedIndex < visibleItems.length) {
                    visibleItems[selectedIndex].click();
                } else if (visibleItems.length > 0) {
                    visibleItems[0].click();
                }
            }
        }
    });

    // Run initialization on DOMReady & HTMX load
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initSidebar);
    } else {
        initSidebar();
    }

    document.addEventListener('htmx:afterSettle', initSidebar);
})();
