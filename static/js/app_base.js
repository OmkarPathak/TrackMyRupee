/* Base-template behaviour, moved out of base.html so the browser can cache it instead of re-parsing
   ~60 KB of inline script on every page. Loaded with defer; strings come from the /jsi18n/ catalogue. */


;/* ---- from base.html line 1159 ---- */
        (function() {
            let loaderTimeout = null;

            // Restart the top progress bar from zero every time (re-adding a class that is already there
            // would not replay its animation, so a second tap would show nothing).
            const restartBar = function () {
                const bar = document.getElementById('global-progress-bar');
                if (!bar) return;
                bar.classList.remove('active');
                bar.style.width = '';
                void bar.offsetWidth;
                bar.classList.add('active');
            };

            // Minimal full-screen feedback that needs nothing but this inline script: used until the
            // deferred skeleton script has loaded (slow mobile connections), so a tap always shows something.
            const showFallback = function () {
                if (document.getElementById('tmr-fallback-loader')) return;
                const el = document.createElement('div');
                el.id = 'tmr-fallback-loader';
                el.setAttribute('role', 'status');
                el.setAttribute('aria-label', 'Loading');
                el.innerHTML = '<div class="tmr-fallback-spinner"></div>';
                document.body.appendChild(el);
            };
            const hideFallback = function () {
                const el = document.getElementById('tmr-fallback-loader');
                if (el && el.parentNode) el.parentNode.removeChild(el);
            };

            window.showLoader = function (destinationUrl) {
                restartBar();
                // Show skeleton loader with page-specific layout (or the fallback if it is not loaded yet)
                if (typeof window.showSkeletonLoader === 'function') {
                    try {
                        window.showSkeletonLoader(destinationUrl || null);
                        hideFallback();
                    } catch (err) {
                        showFallback();
                    }
                } else {
                    showFallback();
                }
                
                // Reset safety timeout (10s) to automatically hide if navigation fails
                clearTimeout(loaderTimeout);
                loaderTimeout = setTimeout(window.hideLoader, 10000);
            };

            window.hideLoader = function () {
                document.documentElement.style.cursor = '';
                hideFallback();
                // Hide skeleton loader
                if (typeof window.hideSkeletonLoader === 'function') {
                    window.hideSkeletonLoader();
                }
                
                if (loaderTimeout) {
                    clearTimeout(loaderTimeout);
                    loaderTimeout = null;
                }
            };

            // 1. Unified Event Handler for Loader Trigger
            const handleTrigger = function (e) {
                // Ignore right clicks or clicks with modifiers
                if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;

                // On touch, a pointerdown is just as likely the start of a scroll as a tap, so only the
                // click (which a scroll never produces) may start the loader; a touch pointerdown left the
                // progress bar stuck "active" and the next real tap then showed nothing.
                if (e.type === 'pointerdown' && e.pointerType === 'touch') return;

                // Find closest element
                const clickable = e.target.closest('a, [onclick], [data-href]');
                if (!clickable) return;

                let shouldShow = false;
                let destinationUrl = null;

                if (clickable.tagName === 'A') {
                    const link = clickable;
                    const href = link.getAttribute('href') || '';
                    if (!href || href === '#' || href.startsWith('#')) return;
                    if (link.target === '_blank' || link.hasAttribute('download') || link.classList.contains('no-loader')) return;
                    if (href.startsWith('mailto:') || href.startsWith('tel:') || href.startsWith('javascript:')) return;
                    if (link.hasAttribute('data-bs-toggle') && href === '#') return;
                    
                    shouldShow = true;
                    destinationUrl = link.href;
                } else if (clickable.hasAttribute('data-href') && !clickable.classList.contains('no-loader')) {
                    shouldShow = true;
                    destinationUrl = clickable.getAttribute('data-href');
                } else if (clickable.hasAttribute('onclick')) {
                    const onclick = clickable.getAttribute('onclick');
                    if (onclick.includes('location.') || onclick.includes('history.')) {
                        shouldShow = true;
                    }
                }

                if (shouldShow) {
                    // INSTANT FEEDBACK
                    restartBar();
                    document.documentElement.style.cursor = 'wait';
                    
                    // Full Skeleton (only on click to avoid multiple triggers on touch-scroll)
                    if (e.type === 'click') {
                        window.showLoader(destinationUrl);
                        e._loaderTriggered = true;
                    }
                }
            };

            // Use pointerdown for the earliest possible feedback on touch/mouse
            document.addEventListener('pointerdown', handleTrigger, true);
            // Use click for the actual navigation commitment
            document.addEventListener('click', handleTrigger, true);

            // 2. Bubbling-phase Click Listener (Cancellation check)
            // If any handler called e.preventDefault(), hide the loader we just showed,
            // UNLESS we already marked this as a valid navigation we want to show a loader for.
            document.addEventListener('click', function (e) {
                if (e.defaultPrevented && !e._loaderTriggered) {
                    window.hideLoader();
                }
            }, false);

            // 3. Form Submit Listener
            document.addEventListener('submit', function (e) {
                if (!e.defaultPrevented) {
                    const form = e.target;
                    if (!form.classList.contains('no-loader') && form.target !== '_blank') {
                        // For form submits, use form action URL or current page URL
                        const formUrl = form.action || window.location.href;
                        window.showLoader(formUrl);
                    }
                }
            });

            // 4. Programmatic Form Submit Interception
            const originalSubmit = HTMLFormElement.prototype.submit;
            HTMLFormElement.prototype.submit = function () {
                if (!this.classList.contains('no-loader') && this.target !== '_blank') {
                    const formUrl = this.action || window.location.href;
                    window.showLoader(formUrl);
                }
                originalSubmit.apply(this, arguments);
            };

            // 5. Robust Navigation Fallbacks & Cleanup
            window.addEventListener('pageshow', () => window.hideLoader());
            window.addEventListener('popstate', () => window.hideLoader());
        })();
    


;/* ---- from base.html line 1315 ---- */
        document.body.addEventListener('htmx:beforeRequest', function (e) {
            const bar = document.getElementById('global-progress-bar');
            if (bar) {
                bar.classList.remove('active');
                void bar.offsetWidth;
                bar.classList.add('active');
            }
            // The 4px bar alone is easy to miss on a phone: also dim the content being replaced (after a
            // short delay so quick responses do not flash).
            const target = e.detail && e.detail.target;
            if (target && target.classList) target.classList.add('tmr-busy');
        });

        // Disable HTMX default auto-focusing and focus-scrolling behavior globally
        if (window.htmx) {
            htmx.config.defaultFocus = false;
            htmx.config.defaultFocusScroll = false;
        }
        document.addEventListener('DOMContentLoaded', function () {
            if (window.htmx) {
                htmx.config.defaultFocus = false;
                htmx.config.defaultFocusScroll = false;
            }
        });

        const clearHtmxLoader = function (e) {
            const bar = document.getElementById('global-progress-bar');
            if (bar) {
                bar.classList.remove('active');
            }
            document.querySelectorAll('.tmr-busy').forEach(function (el) { el.classList.remove('tmr-busy'); });
            // Prevent mobile/PWA soft keyboard auto-opening on HTMX swaps/settles (except inside the New Expense composer)
            if (document.activeElement && 
                (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA') && 
                !document.activeElement.closest('#tmr-composer')) {
                document.activeElement.blur();
            }
        };

        document.body.addEventListener('htmx:afterSettle', clearHtmxLoader);
        document.body.addEventListener('htmx:responseError', clearHtmxLoader);
        document.body.addEventListener('htmx:sendError', clearHtmxLoader);
        document.body.addEventListener('htmx:timeout', clearHtmxLoader);
        document.body.addEventListener('htmx:swapError', clearHtmxLoader);
    


;/* ---- from base.html line 1365 ---- */
        (() => {
            'use strict'

            const getStoredTheme = () => localStorage.getItem('theme')
            const setStoredTheme = theme => localStorage.setItem('theme', theme)

            const setTheme = theme => {
                if (theme === 'auto' && window.matchMedia('(prefers-color-scheme: dark)').matches) {
                    document.documentElement.setAttribute('data-bs-theme', 'dark')
                } else {
                    document.documentElement.setAttribute('data-bs-theme', theme)
                }
            }

            const updateIcon = (theme) => {
                const icons = document.querySelectorAll('.theme-icon-active');
                icons.forEach(icon => {
                    if (theme === 'dark') {
                        icon.classList.remove('bi-moon-stars-fill');
                        icon.classList.add('bi-sun-fill');
                    } else {
                        icon.classList.remove('bi-sun-fill');
                        icon.classList.add('bi-moon-stars-fill');
                    }
                });
            }

            window.addEventListener('DOMContentLoaded', () => {
                const currentTheme = document.documentElement.getAttribute('data-bs-theme');
                updateIcon(currentTheme);

                document.querySelectorAll('.theme-toggle-btn').forEach(toggle => {
                    toggle.addEventListener('click', (e) => {
                        e.preventDefault();
                        const currentTheme = document.documentElement.getAttribute('data-bs-theme');
                        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

                        setStoredTheme(newTheme);
                        setTheme(newTheme);
                        updateIcon(newTheme);
                    });
                });
            });
        })()
    


;/* ---- from base.html line 1412 ---- */
    // On phones the floating bottom navigation and its + button cover the bottom of the screen. Tell every
    // dropdown to keep that strip free, so a menu opened near the bottom opens upwards instead of under it.
    (function () {
        const phone = window.matchMedia('(max-width: 991.98px)');
        document.addEventListener('show.bs.dropdown', function (event) {
            if (!phone.matches || !window.bootstrap) return;
            const instance = bootstrap.Dropdown.getInstance(event.target);
            if (!instance || instance._navClearance) return;
            instance._navClearance = true;
            const own = instance._config.popperConfig;
            instance._config.popperConfig = function () {
                const args = Array.prototype.slice.call(arguments);
                const custom = typeof own === 'function' ? own.apply(null, args) : (own || {});
                try {
                    // Bootstrap passes the default Popper config (as the only argument in 5.3.3)
                    const defaults = args.find(function (a) { return a && a.modifiers; });
                    if (!defaults) return custom;
                    const base = Object.assign({}, defaults, custom);
                    const keep = (base.modifiers || []).filter(function (m) { return m.name !== 'flip' && m.name !== 'preventOverflow'; });
                    const overflow = (defaults.modifiers.find(function (m) { return m.name === 'preventOverflow'; }) || {}).options || {};
                    keep.push({ name: 'flip', options: { padding: { top: 8, bottom: 96 } } });
                    keep.push({ name: 'preventOverflow', options: Object.assign({}, overflow, { padding: { top: 8, bottom: 96 } }) });
                    return Object.assign({}, base, { modifiers: keep });
                } catch (e) {
                    return custom;          // never let this stop a menu from opening
                }
            };
        });
    })();
    


;/* ---- from base.html line 1503 ---- */
    (function() {
        function initSearchableSelects(root) {
            const container = root || document;
            container.querySelectorAll('select.searchable-select, select.form-select:not([multiple]), select.django-multi-select:not([multiple])').forEach(function(sel) {
                if (sel.dataset.searchableInit) return;
                sel.dataset.searchableInit = '1';

                // Hide original select
                sel.style.display = 'none';

                // Build wrapper
                const wrapper = document.createElement('div');
                wrapper.className = 'searchable-select-wrapper';
                sel.parentNode.insertBefore(wrapper, sel);
                wrapper.appendChild(sel);

                // Trigger button (looks like form-select)
                const isSmall = sel.classList.contains('form-select-sm') || sel.classList.contains('form-control-sm');
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'form-select text-start searchable-select-btn' + (isSmall ? ' form-select-sm' : '');
                if (sel.disabled) btn.disabled = true;
                // data-placeholder: an optional select whose empty choice is shown as muted placeholder
                // text, never as a row in the list. A small x clears a chosen value.
                const placeholder = sel.dataset.placeholder || '';
                const clearBtn = document.createElement('button');
                clearBtn.type = 'button';
                clearBtn.className = 'searchable-select-clear';
                clearBtn.setAttribute('aria-label', gettext('Clear'));
                clearBtn.innerHTML = '&times;';
                clearBtn.hidden = true;
                function syncBtn() {
                    const current = sel.options[sel.selectedIndex];
                    if (placeholder) {
                        const has = !!(current && current.value !== '');
                        btn.textContent = has ? current.text : placeholder;
                        btn.classList.toggle('searchable-select-placeholder', !has);
                        clearBtn.hidden = !has || sel.disabled;
                    } else if (current) {
                        btn.textContent = current.text;
                    }
                }
                btn.textContent = sel.options[sel.selectedIndex]?.text || gettext('Select...');
                wrapper.appendChild(btn);
                if (placeholder) {
                    wrapper.appendChild(clearBtn);
                    clearBtn.addEventListener('click', function(e) {
                        e.stopPropagation();
                        sel.value = '';
                        sel.dispatchEvent(new Event('change', { bubbles: true }));
                    });
                    syncBtn();
                }

                // Dropdown panel
                const dropdown = document.createElement('div');
                dropdown.className = 'searchable-select-dropdown';
                wrapper.appendChild(dropdown);

                // Search input
                const search = document.createElement('input');
                search.type = 'text';
                search.className = 'form-control form-control-sm searchable-select-search';
                search.placeholder = gettext('Search...');
                search.autocomplete = 'off';
                dropdown.appendChild(search);

                // Options list
                const list = document.createElement('div');
                list.className = 'searchable-select-list';
                dropdown.appendChild(list);

                function buildOptions(filter) {
                    list.innerHTML = '';
                    const term = (filter || '').toLowerCase();
                    let hasVisible = false;

                    Array.from(sel.children).forEach(function(child) {
                        if (child.tagName === 'OPTGROUP') {
                            const groupLabel = child.label;
                            let groupHasVisible = false;
                            const groupContainer = document.createDocumentFragment();

                            Array.from(child.children).forEach(function(opt) {
                                if (opt.tagName !== 'OPTION') return;
                                if (!opt.value && (placeholder || opt.text.trim() === '---------')) return;
                                const text = opt.text;
                                if (term && text.toLowerCase().indexOf(term) === -1) return;

                                groupHasVisible = true;
                                hasVisible = true;

                                const item = document.createElement('div');
                                item.className = 'searchable-select-item' + (opt.value === sel.value ? ' active' : '') + (opt.disabled ? ' disabled' : '');
                                item.style.paddingLeft = '1.5rem';
                                item.textContent = text;
                                item.dataset.value = opt.value;
                                item.addEventListener('click', function() {
                                    if (opt.disabled) return;
                                    sel.value = opt.value;
                                    sel.dispatchEvent(new Event('change', { bubbles: true }));
                                    syncBtn();
                                    close();
                                });
                                groupContainer.appendChild(item);
                            });

                            if (groupHasVisible) {
                                const header = document.createElement('div');
                                header.className = 'searchable-select-header fw-bold small text-uppercase';
                                header.style.padding = '0.375rem 0.75rem';
                                header.style.pointerEvents = 'none';
                                header.textContent = groupLabel;
                                list.appendChild(header);
                                list.appendChild(groupContainer);
                            }
                        } else if (child.tagName === 'OPTION') {
                            const opt = child;
                            if (!opt.value && (placeholder || opt.text.trim() === '---------')) return;
                            const text = opt.text;
                            if (term && text.toLowerCase().indexOf(term) === -1) return;

                            hasVisible = true;
                            const item = document.createElement('div');
                            item.className = 'searchable-select-item' + (opt.value === sel.value ? ' active' : '') + (opt.disabled ? ' disabled' : '');
                            item.textContent = text;
                            item.dataset.value = opt.value;
                            item.addEventListener('click', function() {
                                if (opt.disabled) return;
                                sel.value = opt.value;
                                sel.dispatchEvent(new Event('change', { bubbles: true }));
                                syncBtn();
                                close();
                            });
                            list.appendChild(item);
                        }
                    });

                    if (!hasVisible) {
                        const empty = document.createElement('div');
                        empty.className = 'searchable-select-empty';
                        empty.textContent = gettext('No matches');
                        list.appendChild(empty);
                    }
                }

                function open() {
                    dropdown.classList.add('show');
                    wrapper.classList.add('open');
                    search.value = '';
                    buildOptions('');
                    if (window.innerWidth >= 768 && !('ontouchstart' in window)) {
                        setTimeout(function() { search.focus(); }, 50);
                    }
                }

                function close() {
                    dropdown.classList.remove('show');
                    wrapper.classList.remove('open');
                }

                function toggle() {
                    dropdown.classList.contains('show') ? close() : open();
                }

                btn.addEventListener('click', function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    // Close any other open searchable selects or multi selects
                    document.querySelectorAll('.searchable-select-dropdown.show, .multi-select-dropdown.show').forEach(function(d) {
                        if (d !== dropdown) {
                            d.classList.remove('show');
                            d.closest('.searchable-select-wrapper, .multi-select-wrapper')?.classList.remove('open');
                        }
                    });
                    toggle();
                });

                search.addEventListener('input', function() {
                    buildOptions(search.value);
                });

                search.addEventListener('keydown', function(e) {
                    if (e.key === 'Escape') close();
                    if (e.key === 'Enter') {
                        e.preventDefault();
                        const first = list.querySelector('.searchable-select-item:not(.disabled)');
                        if (first) first.click();
                    }
                });

                // Close on outside click
                document.addEventListener('click', function(e) {
                    if (!wrapper.contains(e.target)) close();
                });

                // Sync if value changes programmatically
                sel.addEventListener('change', function() {
                    const opt = sel.options[sel.selectedIndex];
                    if (opt) syncBtn();
                });

                // Public refresh API
                sel.searchableSelectRefresh = function() {
                    const opt = sel.options[sel.selectedIndex];
                    if (opt) syncBtn();
                    buildOptions(search.value);
                };
                sel.multiSelectRefresh = sel.searchableSelectRefresh;
            });
        }

        // Run on DOM ready and expose for dynamic forms
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', function() { initSearchableSelects(); });
        } else {
            initSearchableSelects();
        }
        document.addEventListener('htmx:afterSwap', function(e) {
            initSearchableSelects(e.target);
        });
        // When a swap replaces an element that kept its id, htmx copies the server's attributes onto it once the
        // swap has settled. That wipes the display:none the widget put on the original <select>, so both the
        // original and the widget showed. Hide the originals again.
        document.addEventListener('htmx:afterSettle', function(e) {
            var root = e.target && e.target.querySelectorAll ? e.target : document;
            root.querySelectorAll('select[data-searchable-init], select[data-multi-select-init]').forEach(function(sel) {
                sel.style.display = 'none';
            });
        });
        window.initSearchableSelects = initSearchableSelects;
    })();
    


;/* ---- from base.html line 1730 ---- */
    (function() {
        function initMultiSelects(root) {
            const container = root || document;
            container.querySelectorAll('select[multiple].django-multi-select, select[multiple].multi-select').forEach(function(sel) {
                if (sel.dataset.multiSelectInit) return;
                sel.dataset.multiSelectInit = '1';

                // Hide original select
                sel.style.display = 'none';

                // Build wrapper
                const wrapper = document.createElement('div');
                wrapper.className = 'multi-select-wrapper';
                sel.parentNode.insertBefore(wrapper, sel);
                wrapper.appendChild(sel);

                // Trigger button
                const isSmall = sel.classList.contains('form-select-sm') || sel.classList.contains('form-control-sm');
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'form-select text-start multi-select-btn' + (isSmall ? ' form-select-sm' : '');
                if (sel.disabled) btn.disabled = true;
                const ariaLabel = sel.getAttribute('aria-label') || sel.getAttribute('placeholder');
                if (ariaLabel) btn.setAttribute('aria-label', ariaLabel);
                wrapper.appendChild(btn);

                // Dropdown panel
                const dropdown = document.createElement('div');
                dropdown.className = 'multi-select-dropdown';
                wrapper.appendChild(dropdown);

                // Search input
                const search = document.createElement('input');
                search.type = 'text';
                search.className = 'form-control form-control-sm multi-select-search';
                search.placeholder = gettext('Search...');
                search.autocomplete = 'off';
                dropdown.appendChild(search);

                // Action bar: Select all / Clear all
                const actions = document.createElement('div');
                actions.className = 'multi-select-actions';
                actions.innerHTML = `
                    <button type="button" class="btn btn-link btn-sm p-0 multi-select-select-all">${gettext('Select all')}</button>
                    <button type="button" class="btn btn-link btn-sm p-0 multi-select-clear-all text-secondary">${gettext('Clear all')}</button>
                `;
                dropdown.appendChild(actions);

                // Options list
                const list = document.createElement('div');
                list.className = 'multi-select-list';
                dropdown.appendChild(list);

                const selectAllBtn = actions.querySelector('.multi-select-select-all');
                const clearAllBtn = actions.querySelector('.multi-select-clear-all');

                function updateButtonText() {
                    const selected = Array.from(sel.selectedOptions).filter(opt => opt.value !== '' && opt.text.trim() !== '---------');
                    const placeholder = sel.getAttribute('placeholder') || sel.getAttribute('aria-label') || gettext('Select...');
                    if (selected.length === 0) {
                        btn.textContent = placeholder;
                    } else if (selected.length === 1) {
                        btn.textContent = selected[0].text;
                    } else {
                        btn.textContent = `${selected[0].text} +${selected.length - 1}`;
                    }
                }

                function buildOptions(filter) {
                    list.innerHTML = '';
                    const term = (filter || '').toLowerCase();
                    let hasVisible = false;

                    Array.from(sel.children).forEach(function(child) {
                        if (child.tagName === 'OPTGROUP') {
                            const groupLabel = child.label;
                            let groupHasVisible = false;
                            const groupContainer = document.createDocumentFragment();

                            Array.from(child.children).forEach(function(opt) {
                                if (opt.tagName !== 'OPTION') return;
                                if (!opt.value && opt.text.trim() === '---------') return;
                                const text = opt.text;
                                if (term && text.toLowerCase().indexOf(term) === -1) return;

                                groupHasVisible = true;
                                hasVisible = true;

                                const item = createOptionItem(opt, text, true);
                                groupContainer.appendChild(item);
                            });

                            if (groupHasVisible) {
                                const header = document.createElement('div');
                                header.className = 'multi-select-header fw-bold small text-uppercase';
                                header.style.padding = '0.375rem 0.75rem';
                                header.style.pointerEvents = 'none';
                                header.textContent = groupLabel;
                                list.appendChild(header);
                                list.appendChild(groupContainer);
                            }
                        } else if (child.tagName === 'OPTION') {
                            const opt = child;
                            if (!opt.value && opt.text.trim() === '---------') return;
                            const text = opt.text;
                            if (term && text.toLowerCase().indexOf(term) === -1) return;

                            hasVisible = true;
                            const item = createOptionItem(opt, text, false);
                            list.appendChild(item);
                        }
                    });

                    if (!hasVisible) {
                        const empty = document.createElement('div');
                        empty.className = 'multi-select-empty';
                        empty.textContent = gettext('No matches');
                        list.appendChild(empty);
                    }
                }

                function createOptionItem(opt, text, inGroup) {
                    const item = document.createElement('div');
                    item.className = 'multi-select-item' + (opt.selected ? ' active' : '') + (opt.disabled ? ' disabled' : '');
                    if (inGroup) item.style.paddingLeft = '1.5rem';

                    const cb = document.createElement('input');
                    cb.type = 'checkbox';
                    cb.className = 'form-check-input';
                    cb.checked = opt.selected;
                    if (opt.disabled) cb.disabled = true;

                    const label = document.createElement('span');
                    label.className = 'multi-select-item-text';
                    label.textContent = text;

                    item.appendChild(cb);
                    item.appendChild(label);

                    item.addEventListener('click', function(e) {
                        if (opt.disabled) return;
                        if (e.target !== cb) {
                            cb.checked = !cb.checked;
                        }
                        opt.selected = cb.checked;
                        item.classList.toggle('active', opt.selected);
                        updateButtonText();
                        sel.dispatchEvent(new Event('change', { bubbles: true }));
                    });

                    return item;
                }

                selectAllBtn.addEventListener('click', function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    const term = (search.value || '').toLowerCase();
                    Array.from(sel.options).forEach(function(opt) {
                        if (!opt.value && opt.text.trim() === '---------') return;
                        if (opt.disabled) return;
                        if (term && opt.text.toLowerCase().indexOf(term) === -1) return;
                        opt.selected = true;
                    });
                    buildOptions(search.value);
                    updateButtonText();
                    sel.dispatchEvent(new Event('change', { bubbles: true }));
                });

                clearAllBtn.addEventListener('click', function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    const term = (search.value || '').toLowerCase();
                    Array.from(sel.options).forEach(function(opt) {
                        if (!opt.value && opt.text.trim() === '---------') return;
                        if (opt.disabled) return;
                        if (term && opt.text.toLowerCase().indexOf(term) === -1) return;
                        opt.selected = false;
                    });
                    buildOptions(search.value);
                    updateButtonText();
                    sel.dispatchEvent(new Event('change', { bubbles: true }));
                });

                function open() {
                    dropdown.classList.add('show');
                    wrapper.classList.add('open');
                    search.value = '';
                    buildOptions('');
                    if (window.innerWidth >= 768 && !('ontouchstart' in window)) {
                        setTimeout(function() { search.focus(); }, 50);
                    }
                }

                function close() {
                    dropdown.classList.remove('show');
                    wrapper.classList.remove('open');
                }

                function toggle() {
                    dropdown.classList.contains('show') ? close() : open();
                }

                btn.addEventListener('click', function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    // Close any other open dropdowns (searchable-select or multi-select)
                    document.querySelectorAll('.multi-select-dropdown.show, .searchable-select-dropdown.show').forEach(function(d) {
                        if (d !== dropdown) {
                            d.classList.remove('show');
                            d.closest('.multi-select-wrapper, .searchable-select-wrapper')?.classList.remove('open');
                        }
                    });
                    toggle();
                });

                search.addEventListener('input', function() {
                    buildOptions(search.value);
                });

                search.addEventListener('keydown', function(e) {
                    if (e.key === 'Escape') close();
                });

                // Close on outside click
                document.addEventListener('click', function(e) {
                    if (!wrapper.contains(e.target)) close();
                });

                // Close on Escape globally
                document.addEventListener('keydown', function(e) {
                    if (e.key === 'Escape' && dropdown.classList.contains('show')) close();
                });

                // Sync if selected options change programmatically
                sel.addEventListener('change', function() {
                    updateButtonText();
                    if (dropdown.classList.contains('show')) {
                        buildOptions(search.value);
                    }
                });

                // Initial text setup
                updateButtonText();

                // Public refresh API
                sel.multiSelectRefresh = function() {
                    updateButtonText();
                    buildOptions(search.value);
                };
                sel.searchableSelectRefresh = sel.multiSelectRefresh;
            });
        }

        // Run on DOM ready, expose globally, and bind to HTMX
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', function() { initMultiSelects(); });
        } else {
            initMultiSelects();
        }
        document.addEventListener('htmx:afterSwap', function(e) {
            initMultiSelects(e.target);
        });
        window.initMultiSelects = initMultiSelects;
    })();
    


;/* ---- from base.html line 2270 ---- */
        document.addEventListener('submit', function(e) {
            const form = e.target;

            // Let HTMX handle boosted/AJAX forms; do not force full-page navigation.
            const isHtmxForm = form.hasAttribute('hx-get')
                || form.hasAttribute('hx-post')
                || form.hasAttribute('hx-put')
                || form.hasAttribute('hx-patch')
                || form.hasAttribute('hx-delete')
                || form.hasAttribute('data-hx-get')
                || form.hasAttribute('data-hx-post')
                || form.hasAttribute('data-hx-put')
                || form.hasAttribute('data-hx-patch')
                || form.hasAttribute('data-hx-delete')
                || form.closest('[hx-boost="true"]')
                || form.closest('[data-hx-boost="true"]');

            if (isHtmxForm) {
                return;
            }

            // Intercept GET forms without a specific target to clean up empty query parameters
            if (form.method && form.method.toLowerCase() === 'get' && (!form.target || form.target === '_self')) {
                e.preventDefault();
                
                const url = new URL(form.action || window.location.href);
                // Clear existing query params so we only use the newly submitted ones
                url.search = '';
                
                const formData = new FormData(form);
                for (const [key, value] of formData.entries()) {
                    if (value.trim() !== '') {
                        url.searchParams.append(key, value);
                    }
                }
                
                window.location.href = url.toString();
            }
        });
    


;/* ---- from base.html line 2313 ---- */
    window.handleTimePeriodChange = function(input) {
        if (!input) return;
        var val = input.value;
        var form = input.closest('form');
        if (!form) return;

        var pills = form.querySelectorAll('input[name="time_period"]');
        pills.forEach(function(radio) {
            var label = radio.closest('label');
            if (!label) return;
            if (radio.value === val) {
                label.className = 'btn btn-sm rounded-pill fw-medium px-3 transition-all d-inline-flex align-items-center justify-content-center time-period-pill btn-primary text-white border-primary shadow-sm active';
                label.style.backgroundColor = 'var(--color-secondary, #0F7657)';
                label.style.borderColor = 'var(--color-secondary, #0F7657)';
                label.style.color = '#ffffff';
            } else {
                label.className = 'btn btn-sm rounded-pill fw-medium px-3 transition-all d-inline-flex align-items-center justify-content-center time-period-pill btn-light text-body border-0';
                label.style.backgroundColor = 'var(--bs-tertiary-bg)';
                label.style.borderColor = '';
                label.style.color = '';
            }
        });

        var customDateWrap = form.querySelector('.custom-date-container');
        if (val === 'custom') {
            if (customDateWrap) {
                customDateWrap.classList.remove('d-none');
            }
        } else {
            if (customDateWrap) {
                customDateWrap.classList.add('d-none');
            }
            var f = document.createElement('input');
            f.type = 'hidden';
            f.name = 'pill_clicked';
            f.value = 'true';
            form.appendChild(f);
            form.dispatchEvent(new Event('submit', { cancelable: true }));
        }
    };
    


;/* ---- from base.html line 2357 ---- */
    (function () {
        'use strict';

        var MOBILE_BP = 768;

        /* ---- Singleton drawer elements ---- */
        var backdrop = null;
        var drawer = null;
        var drawerBody = null;
        var drawerTitle = null;
        var currentCollapseId = null;   // ID of the collapse panel we borrowed content from
        var currentCollapseEl = null;   // the actual collapse DOM element
        var originalParent = null;      // where to put the form back
        var originalNextSibling = null; // sibling to restore insertion order
        var borrowedForm = null;        // the actual form element we moved

        function isMobile() {
            return window.innerWidth <= MOBILE_BP;
        }

        function buildDrawer() {
            if (drawer) return; // already built

            /* Backdrop */
            backdrop = document.createElement('div');
            backdrop.className = 'mobile-filter-backdrop';
            backdrop.setAttribute('aria-hidden', 'true');
            document.body.appendChild(backdrop);

            /* Drawer shell */
            drawer = document.createElement('div');
            drawer.className = 'mobile-filter-drawer';
            drawer.setAttribute('role', 'dialog');
            drawer.setAttribute('aria-modal', 'true');
            drawer.setAttribute('aria-label', 'Filters');
            drawer.innerHTML =
                '<div class="mobile-filter-drawer__handle"></div>' +
                '<div class="mobile-filter-drawer__header">' +
                    '<h2 class="mobile-filter-drawer__title">Filters</h2>' +
                    '<button type="button" class="mobile-filter-drawer__close" aria-label="Close filters">' +
                        '<i class="bi bi-x-lg"></i>' +
                    '</button>' +
                '</div>' +
                '<div class="mobile-filter-drawer__body"></div>';
            document.body.appendChild(drawer);

            drawerBody = drawer.querySelector('.mobile-filter-drawer__body');
            drawerTitle = drawer.querySelector('.mobile-filter-drawer__title');

            /* Close button */
            drawer.querySelector('.mobile-filter-drawer__close').addEventListener('click', closeDrawer);

            /* Backdrop tap */
            backdrop.addEventListener('click', closeDrawer);

            /* Swipe-down to close: only trigger when dragging down from the drag handle or header,
               or when dragging down at the top of drawerBody (scrollTop <= 0) outside dropdown menus & inputs */
            var touchStartY = 0;
            var touchStartScrollTop = 0;
            var isHandleOrHeaderTouch = false;
            var isDropdownOrInputTouch = false;

            drawer.addEventListener('touchstart', function (e) {
                touchStartY = e.touches[0].clientY;
                touchStartScrollTop = drawerBody ? drawerBody.scrollTop : 0;
                isHandleOrHeaderTouch = !!e.target.closest('.mobile-filter-drawer__handle, .mobile-filter-drawer__header');
                isDropdownOrInputTouch = !!e.target.closest('.dropdown-menu, .multiselect-container, .multi-select-dropdown, .searchable-select-dropdown, select, input, option, label');
            }, { passive: true });

            drawer.addEventListener('touchend', function (e) {
                var dy = e.changedTouches[0].clientY - touchStartY;
                if (isHandleOrHeaderTouch && dy > 50) {
                    closeDrawer();
                } else if (touchStartScrollTop <= 0 && !isDropdownOrInputTouch && dy > 90) {
                    closeDrawer();
                }
            }, { passive: true });
        }

        function openDrawer(toggleBtn) {
            if (!isMobile()) return;

            buildDrawer();

            /* Find the linked collapse */
            var collapseTarget = toggleBtn.getAttribute('data-bs-target');
            if (!collapseTarget) return;
            var collapseEl = document.querySelector(collapseTarget);
            if (!collapseEl) return;

            /* Prevent Bootstrap from toggling collapse on mobile */
            /* (we handle show/hide ourselves) */
            currentCollapseId = collapseTarget;
            currentCollapseEl = collapseEl;

            /* Get the filter form inside the collapse */
            var form = collapseEl.querySelector('form');
            if (!form) return;

            /* Immediately kill any Bootstrap-toggled show/collapsing state
               so the inline collapse never becomes visible */
            collapseEl.classList.remove('show', 'collapsing');
            collapseEl.style.height = '';
            /* Also kill it after the next frame in case Bootstrap adds it async */
            requestAnimationFrame(function () {
                collapseEl.classList.remove('show', 'collapsing');
                collapseEl.style.height = '';
            });

            /* Move the form into the drawer body */
            originalParent = form.parentElement;
            originalNextSibling = form.nextSibling;
            borrowedForm = form;
            drawerBody.appendChild(form);

            /* Update drawer title based on page context */
            var pageTitle = 'Filters';
            var shell = collapseEl.closest('[id$="-shell"]');
            if (shell) {
                var h2 = shell.querySelector('h2, h3');
                if (h2) {
                    pageTitle = h2.textContent.trim().split('\n')[0].trim();
                    if (pageTitle.length > 20) pageTitle = 'Filters';
                }
            }
            drawerTitle.textContent = 'Filters';

            /* Show drawer */
            drawer.classList.add('is-open');
            backdrop.style.display = 'block';

            /* Force reflow then animate in */
            drawer.getBoundingClientRect();
            backdrop.getBoundingClientRect();

            backdrop.classList.add('is-visible');
            drawer.classList.add('is-animating-in');

            /* Lock body scroll */
            document.body.style.overflow = 'hidden';

            /* Focus the drawer for accessibility */
            drawer.setAttribute('tabindex', '-1');
            drawer.focus();
        }

        function closeDrawer() {
            if (!drawer || !drawer.classList.contains('is-open')) return;

            /* Slide out */
            drawer.classList.remove('is-animating-in');
            backdrop.classList.remove('is-visible');

            /* Wait for transition to finish, then clean up */
            var onTransEnd = function () {
                drawer.removeEventListener('transitionend', onTransEnd);
                drawer.classList.remove('is-open');
                backdrop.style.display = 'none';

                /* Return the form to its original location */
                if (borrowedForm && originalParent) {
                    if (originalNextSibling) {
                        originalParent.insertBefore(borrowedForm, originalNextSibling);
                    } else {
                        originalParent.appendChild(borrowedForm);
                    }
                }

                /* Ensure the collapse stays hidden — Bootstrap may have added
                   'show' or 'collapsing' while we were intercepting the click */
                if (currentCollapseEl) {
                    currentCollapseEl.classList.remove('show', 'collapsing');
                    currentCollapseEl.style.height = '';
                }

                borrowedForm = null;
                originalParent = null;
                originalNextSibling = null;
                currentCollapseId = null;
                currentCollapseEl = null;

                /* Restore body scroll */
                document.body.style.overflow = '';
            };
            drawer.addEventListener('transitionend', onTransEnd);
        }

        /* ---- Intercept filter-toggle-btn clicks ---- */
        document.addEventListener('click', function (e) {
            var btn = e.target.closest('.filter-toggle-btn');
            if (!btn) return;

            if (!isMobile()) return; // desktop: let Bootstrap collapse handle it

            /* Prevent Bootstrap collapse from running on mobile */
            e.preventDefault();
            e.stopPropagation();

            openDrawer(btn);
        }, true /* capture phase so we intercept before Bootstrap */);

        /* ---- Re-init on HTMX swaps (content reloads) ---- */
        document.addEventListener('htmx:afterSwap', function () {
            /* If drawer is open and content was swapped, close it */
            if (drawer && drawer.classList.contains('is-open')) {
                closeDrawer();
            }
        });

        /* ---- Close on ESC ---- */
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') closeDrawer();
        });

    })();

    /* ---- Ensure open action dropdowns always float above sibling cards/rows & escape overflow clipping ---- */
    document.addEventListener('show.bs.dropdown', function (e) {
        var card = e.target.closest('.card, .expense-card, tr');
        if (card) {
            card.style.setProperty('z-index', '1050', 'important');
        }
        var dropdown = e.target.closest('.dropdown');
        if (dropdown) {
            dropdown.style.setProperty('z-index', '1055', 'important');
        }
        var tableResponsive = e.target.closest('.table-responsive');
        if (tableResponsive) {
            tableResponsive.style.setProperty('overflow', 'visible', 'important');
        }
        var parentCard = e.target.closest('.card');
        if (parentCard) {
            if (parentCard.classList.contains('overflow-hidden')) {
                parentCard.dataset.hadOverflowHidden = 'true';
                parentCard.classList.remove('overflow-hidden');
            }
            parentCard.style.setProperty('overflow', 'visible', 'important');
        }
    });

    document.addEventListener('hidden.bs.dropdown', function (e) {
        var card = e.target.closest('.card, .expense-card, tr');
        if (card) {
            card.style.removeProperty('z-index');
        }
        var dropdown = e.target.closest('.dropdown');
        if (dropdown) {
            dropdown.style.removeProperty('z-index');
        }
        var tableResponsive = e.target.closest('.table-responsive');
        if (tableResponsive) {
            tableResponsive.style.removeProperty('overflow');
        }
        var parentCard = e.target.closest('.card');
        if (parentCard) {
            parentCard.style.removeProperty('overflow');
            if (parentCard.dataset.hadOverflowHidden === 'true') {
                delete parentCard.dataset.hadOverflowHidden;
                parentCard.classList.add('overflow-hidden');
            }
        }
    });
    
