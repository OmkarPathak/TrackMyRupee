/**
 * TrackMyRupee - Declarative Chip-Based Filter Toolbar System
 */

class TMRFilterSystem {
  constructor(pageKey) {
    this.pageKey = pageKey;
    this.storageKey = `tmr_filters:${pageKey}`;
    this.container = document.getElementById(`tmr-toolbar-${pageKey}`);
    if (!this.container) return;

    this.configScript = document.getElementById(`tmr-filter-config-${pageKey}`);
    this.stateScript = document.getElementById(`tmr-applied-state-${pageKey}`);
    this.chipRow = document.getElementById(`tmr-chip-row-${pageKey}`);

    this.config = this.configScript ? JSON.parse(this.configScript.textContent) : {};
    this.state = this.stateScript ? JSON.parse(this.stateScript.textContent) : {};

    this.activePopover = null;
    this.dynamicOptionsCache = {};
    this.optionLabelCache = {};

    // Bound once so they can be added/removed cleanly across htmx re-inits
    this._onDocumentClick = this.handleDocumentClick.bind(this);
    this._onDocumentKeydown = this.handleDocumentKeydown.bind(this);

    this.init();
  }

  init() {
    this.restoreStateFromStorageOrURL();
    this.bindStaticEvents();
    this.renderChipRow();
  }

  // Called before a fresh instance takes over (e.g. after an htmx swap replaces the toolbar DOM)
  destroy() {
    document.removeEventListener('click', this._onDocumentClick);
    document.removeEventListener('keydown', this._onDocumentKeydown);
    if (this._onTabKeydown) {
      document.removeEventListener('keydown', this._onTabKeydown);
    }
    this.closePopover();
  }

  handleDocumentClick(e) {
    if (this.activePopover && !this.activePopover.contains(e.target)) {
      this.closePopover();
    }
  }

  handleDocumentKeydown(e) {
    if (e.key === 'Escape' && this.activePopover) {
      this.closePopover();
    }
  }

  restoreStateFromStorageOrURL() {
    const urlParams = new URLSearchParams(window.location.search);
    const hasUrlParams = Array.from(urlParams.keys()).some(k => 
      k === 'search' || k === 'time_period' || k === 'sort' || this.isKnownFilterKey(k)
    );

    if (hasUrlParams) {
      // URL params present: read from URL and sync to sessionStorage
      this.syncStateFromURL(urlParams);
      this.saveStateToStorage();
    } else {
      // No URL params: check sessionStorage
      const stored = sessionStorage.getItem(this.storageKey);
      if (stored) {
        try {
          const parsed = JSON.parse(stored);
          this.state = this.sanitizeState(parsed);
          // If stored state is non-default, trigger URL update and data reload if needed
          if (this.hasActiveFilters(this.state)) {
            this.applyStateToURLAndFetch(false);
          }
        } catch (e) {
          console.warn('Failed to parse stored filter state:', e);
        }
      }
    }
  }

  isKnownFilterKey(key) {
    return this.config.filters && this.config.filters.some(f => f.key === key);
  }

  sanitizeState(rawState) {
    const clean = {
      search: rawState.search || '',
      time_period: rawState.time_period || this.config.default_time_range || 'this_month',
      start_date: rawState.start_date || '',
      end_date: rawState.end_date || '',
      sort: rawState.sort || this.config.default_sort || 'date_desc',
      filters: {},
    };

    if (rawState.filters && typeof rawState.filters === 'object') {
      for (const [key, vals] of Object.entries(rawState.filters)) {
        if (this.isKnownFilterKey(key)) {
          clean.filters[key] = Array.isArray(vals) ? vals : [vals];
        }
      }
    }
    return clean;
  }

  syncStateFromURL(urlParams) {
    this.state.search = urlParams.get('search') || urlParams.get('q') || '';
    this.state.time_period = urlParams.get('time_period') || this.config.default_time_range || 'this_month';
    this.state.start_date = urlParams.get('start_date') || '';
    this.state.end_date = urlParams.get('end_date') || '';
    this.state.sort = urlParams.get('sort') || this.config.default_sort || 'date_desc';

    this.state.filters = {};
    if (this.config.filters) {
      for (const f of this.config.filters) {
        const vals = urlParams.getAll(f.key).filter(v => v && v.trim());
        if (vals.length > 0) {
          this.state.filters[f.key] = vals;
        }
      }
    }
  }

  saveStateToStorage() {
    sessionStorage.setItem(this.storageKey, JSON.stringify(this.state));
  }

  hasActiveFilters(state) {
    if (!state) state = this.state;
    const hasSearch = !!(state.search && state.search.trim());
    const hasTime = this.config.supports_time_period !== false && state.time_period && state.time_period !== (this.config.default_time_range || 'this_month');
    const hasChips = Object.keys(state.filters || {}).length > 0;
    return hasSearch || hasTime || hasChips;
  }

  bindStaticEvents() {
    // Search input
    const searchInput = this.container.querySelector('.tmr-search-input');
    const searchBox = this.container.querySelector('.tmr-search-box');
    const searchClearBtn = this.container.querySelector('.tmr-search-clear-btn');
    if (searchInput) {
      let isExplicitUserInteraction = !!window.__tmrPreserveSearchFocus;

      const allowFocus = () => {
        isExplicitUserInteraction = true;
        searchInput.dataset.userActive = '1';
      };

      if (searchBox) {
        searchBox.addEventListener('pointerdown', allowFocus);
        searchBox.addEventListener('mousedown', allowFocus);
        searchBox.addEventListener('touchstart', allowFocus, { passive: true });
      }

      searchInput.addEventListener('pointerdown', allowFocus);
      searchInput.addEventListener('mousedown', allowFocus);
      searchInput.addEventListener('touchstart', allowFocus, { passive: true });

      // Support explicit keyboard Tab navigation into the search input
      this._onTabKeydown = (e) => {
        if (e.key === 'Tab') {
          isExplicitUserInteraction = true;
          searchInput.dataset.userActive = '1';
          setTimeout(() => {
            if (document.activeElement !== searchInput) {
              delete searchInput.dataset.userActive;
              isExplicitUserInteraction = false;
            }
          }, 200);
        }
      };
      document.addEventListener('keydown', this._onTabKeydown);

      // Prevent any automatic focusing on initial load, bfcache, or swaps
      searchInput.addEventListener('focus', (e) => {
        if (!isExplicitUserInteraction && !window.__tmrPreserveSearchFocus) {
          e.preventDefault();
          delete searchInput.dataset.userActive;
          searchInput.blur();
        }
      });

      searchInput.addEventListener('blur', () => {
        isExplicitUserInteraction = false;
        delete searchInput.dataset.userActive;
      });

      // If preserved from an active search input debounce swap, restore focus & caret
      if (window.__tmrPreserveSearchFocus) {
        window.__tmrPreserveSearchFocus = false;
        isExplicitUserInteraction = true;
        searchInput.dataset.userActive = '1';
        searchInput.focus();
        const len = searchInput.value.length;
        searchInput.setSelectionRange(len, len);
      } else {
        // Enforce blur across initial execution, microtasks, and page lifecycle events
        if (document.activeElement === searchInput) {
          searchInput.blur();
        }
        [0, 50, 150, 300].forEach(delay => {
          setTimeout(() => {
            if (!isExplicitUserInteraction && document.activeElement === searchInput) {
              searchInput.blur();
            }
          }, delay);
        });

        window.addEventListener('load', () => {
          if (!isExplicitUserInteraction && document.activeElement === searchInput) {
            searchInput.blur();
          }
        }, { once: true });

        window.addEventListener('pageshow', () => {
          if (!isExplicitUserInteraction && document.activeElement === searchInput) {
            searchInput.blur();
          }
        });
      }

      let debounceTimer = null;
      const updateSearchClearVisibility = () => {
        if (searchClearBtn) {
          const hasText = !!(searchInput.value && searchInput.value.length > 0);
          searchClearBtn.style.display = hasText ? 'inline-flex' : 'none';
        }
      };

      searchInput.addEventListener('input', (e) => {
        updateSearchClearVisibility();
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
          const newSearch = e.target.value.trim();
          const prevSearch = (this.state.search || '').trim();
          if (newSearch === prevSearch) return;

          this.state.search = newSearch;
          if (newSearch) {
            this.trackFilterEvent('filter_search_entered', { query_length: newSearch.length });
          }
          if (document.activeElement === searchInput) {
            window.__tmrPreserveSearchFocus = true;
          }
          this.onStateChanged();
        }, 450);
      });

      searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          clearTimeout(debounceTimer);
          const newSearch = searchInput.value.trim();
          const prevSearch = (this.state.search || '').trim();
          if (newSearch !== prevSearch) {
            this.state.search = newSearch;
            if (document.activeElement === searchInput) {
              window.__tmrPreserveSearchFocus = true;
            }
            this.onStateChanged();
          }
        }
      });

      const clearSearch = () => {
        if (!searchInput.value && !this.state.search) return;
        this.trackFilterEvent('filter_search_cleared');
        searchInput.value = '';
        updateSearchClearVisibility();
        this.state.search = '';
        if (debounceTimer) {
          clearTimeout(debounceTimer);
        }
        const isFinePointer = window.matchMedia && window.matchMedia('(pointer: fine)').matches;
        if (isFinePointer) {
          window.__tmrPreserveSearchFocus = true;
          allowFocus();
          searchInput.focus();
        } else {
          window.__tmrPreserveSearchFocus = false;
        }
        this.onStateChanged();
      };

      if (searchClearBtn) {
        updateSearchClearVisibility();
        searchClearBtn.addEventListener('pointerdown', (e) => e.stopPropagation());
        searchClearBtn.addEventListener('mousedown', (e) => e.stopPropagation());
        searchClearBtn.addEventListener('touchstart', (e) => e.stopPropagation(), { passive: true });
        searchClearBtn.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          clearSearch();
        });
      }

      searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && searchInput.value) {
          e.preventDefault();
          e.stopPropagation();
          clearSearch();
        }
      });
    }

    // Time Dropdown
    const timeBtn = this.container.querySelector('#tmr-time-dropdown-btn');
    if (timeBtn) {
      timeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.toggleTimePopover(timeBtn);
      });
    }

    // + Filter Button
    const addFilterBtn = this.container.querySelector('#tmr-add-filter-btn');
    if (addFilterBtn) {
      addFilterBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.toggleAddFilterPopover(addFilterBtn);
      });
    }

    // Sort Dropdown
    const sortBtn = this.container.querySelector('#tmr-sort-dropdown-btn');
    if (sortBtn) {
      sortBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.toggleSortPopover(sortBtn);
      });
    }

    // Document Outside Click & Escape Key
    document.addEventListener('click', this._onDocumentClick);
    document.addEventListener('keydown', this._onDocumentKeydown);
  }

  renderChipRow() {
    if (!this.chipRow) return;
    this.chipRow.innerHTML = '';

    const activeKeys = Object.keys(this.state.filters || {});
    if (activeKeys.length === 0) return;

    activeKeys.forEach(filterKey => {
      const filterDef = this.getFilterDef(filterKey);
      if (!filterDef) return;

      const selectedVals = this.state.filters[filterKey] || [];
      const isUnset = selectedVals.length === 0;

      const chip = document.createElement('div');
      chip.className = `tmr-chip ${isUnset ? 'tmr-chip-unset' : ''}`;
      chip.dataset.filterKey = filterKey;

      // Label
      const labelSpan = document.createElement('span');
      labelSpan.className = 'tmr-chip-label';
      labelSpan.textContent = filterDef.label;
      chip.appendChild(labelSpan);

      // Value Button
      const valBtn = document.createElement('button');
      valBtn.type = 'button';
      valBtn.className = 'tmr-chip-value-btn';
      
      const valText = this.formatChipValueText(filterDef, selectedVals);
      valBtn.innerHTML = `<span>${valText}</span><i class="bi bi-caret-down-fill ms-1" style="font-size: 0.65rem;"></i>`;
      valBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.toggleChipValuePopover(valBtn, filterDef);
      });
      chip.appendChild(valBtn);

      // Async fetch dynamic options if label is currently uncached (showing raw ID)
      if (!isUnset && filterDef.source === 'dynamic') {
        const firstVal = String(selectedVals[0]);
        if (!this.optionLabelCache[filterKey] || !this.optionLabelCache[filterKey][firstVal]) {
          this.fetchFilterOptions(filterDef).then(() => {
            const updatedText = this.formatChipValueText(filterDef, selectedVals);
            const textSpan = valBtn.querySelector('span');
            if (textSpan) {
              textSpan.textContent = updatedText;
            }
          });
        }
      }

      // Remove X Button
      const removeBtn = document.createElement('button');
      removeBtn.type = 'button';
      removeBtn.className = 'tmr-chip-remove-btn';
      removeBtn.innerHTML = '<i class="bi bi-x-lg"></i>';
      removeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.removeFilterChip(filterKey);
      });
      chip.appendChild(removeBtn);

      this.chipRow.appendChild(chip);
    });

    // Clear All Button
    const clearAllBtn = document.createElement('button');
    clearAllBtn.type = 'button';
    clearAllBtn.className = 'tmr-clear-all-btn';
    clearAllBtn.textContent = 'Clear all';
    clearAllBtn.addEventListener('click', () => {
      this.clearAllChips();
    });
    this.chipRow.appendChild(clearAllBtn);
  }

  cacheOptionLabels(filterKey, opts) {
    if (!this.optionLabelCache[filterKey]) {
      this.optionLabelCache[filterKey] = {};
    }
    if (Array.isArray(opts)) {
      opts.forEach(opt => {
        if (typeof opt === 'object' && opt !== null && opt.value !== undefined) {
          this.optionLabelCache[filterKey][String(opt.value)] = String(opt.label || opt.value);
        } else if (opt !== undefined && opt !== null) {
          this.optionLabelCache[filterKey][String(opt)] = String(opt);
        }
      });
    }
  }

  getOptionLabel(filterDef, val) {
    if (val === undefined || val === null || val === '') return '';
    const key = filterDef.key;
    const strVal = String(val);

    if (this.optionLabelCache[key] && this.optionLabelCache[key][strVal]) {
      return this.optionLabelCache[key][strVal];
    }

    if (filterDef.options && Array.isArray(filterDef.options)) {
      for (const opt of filterDef.options) {
        if (typeof opt === 'object' && opt !== null) {
          if (String(opt.value) === strVal) {
            const lbl = String(opt.label || opt.value);
            this.cacheOptionLabels(key, [opt]);
            return lbl;
          }
        } else if (String(opt) === strVal) {
          return String(opt);
        }
      }
    }
    return strVal;
  }

  formatChipValueText(filterDef, selectedVals) {
    if (!selectedVals || selectedVals.length === 0) {
      return 'Any';
    }
    const firstVal = selectedVals[0];
    const firstLabel = this.getOptionLabel(filterDef, firstVal);
    const displayFirst = firstLabel.length > 18 ? firstLabel.substring(0, 18) + '...' : firstLabel;

    if (selectedVals.length === 1) {
      return displayFirst;
    }
    return `${displayFirst} +${selectedVals.length - 1}`;
  }

  getFilterDef(key) {
    return this.config.filters ? this.config.filters.find(f => f.key === key) : null;
  }

  removeFilterChip(key) {
    this.trackFilterEvent('filter_chip_removed', { filter_key: key });
    delete this.state.filters[key];
    this.onStateChanged();
  }

  clearAllChips() {
    this.trackFilterEvent('filter_cleared_all');
    this.state.filters = {};
    this.onStateChanged();
  }

  closePopover() {
    if (this.activePopover) {
      this.activePopover.remove();
      this.activePopover = null;
    }
    if (this.activeBackdrop) {
      this.activeBackdrop.remove();
      this.activeBackdrop = null;
      document.body.style.overflow = '';
    }
  }

  isMobileViewport() {
    return window.innerWidth < 768;
  }

  trackFilterEvent(eventName, properties = {}) {
    try {
      const consentGranted = localStorage.getItem('cookieConsent') === 'accepted';
      const eventData = {
        page: this.pageKey,
        device: this.isMobileViewport() ? 'mobile' : 'desktop',
        ...properties,
      };

      // PostHog Product Analytics
      if (typeof window.posthog !== 'undefined' && typeof window.posthog.capture === 'function' && consentGranted) {
        window.posthog.capture(eventName, eventData);
      }

      // Google Analytics 4
      if (typeof window.gtag === 'function' && consentGranted) {
        window.gtag('event', eventName, eventData);
      }
    } catch (e) {
      // Analytics failures must never break the filter UI
    }
  }

  openPopover(anchorEl, contentHtml, onMounted) {
    this.closePopover();

    const popover = document.createElement('div');
    popover.className = 'tmr-popover-menu';
    popover.innerHTML = contentHtml;

    if (this.isMobileViewport()) {
      // On mobile the popover becomes a floating bottom sheet (see tmr_filter.css),
      // so it doesn't need anchor-relative positioning - just a dimming backdrop.
      const backdrop = document.createElement('div');
      backdrop.className = 'tmr-popover-backdrop';
      backdrop.addEventListener('click', () => this.closePopover());
      document.body.appendChild(backdrop);
      this.activeBackdrop = backdrop;
      document.body.style.overflow = 'hidden';
    } else {
      this.positionPopover(anchorEl, popover);
    }

    document.body.appendChild(popover);
    this.activePopover = popover;

    if (onMounted) {
      onMounted(popover);
    }
  }

  positionPopover(anchorEl, popover) {
    const rect = anchorEl.getBoundingClientRect();
    const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
    const scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;

    popover.style.top = `${rect.bottom + scrollTop + 6}px`;
    popover.style.left = `${rect.left + scrollLeft}px`;
  }

  // --- Popover 1: + Filter Menu ---
  toggleAddFilterPopover(anchorEl) {
    this.trackFilterEvent('filter_menu_opened');
    const filters = this.config.filters || [];
    const html = `
      <div class="tmr-popover-header">
        <input type="text" class="tmr-popover-search" placeholder="Search filters...">
      </div>
      <ul class="tmr-popover-list">
        ${filters.map(f => {
          const isAdded = Object.prototype.hasOwnProperty.call(this.state.filters, f.key);
          return `
            <li class="tmr-popover-item ${isAdded ? 'selected' : ''}" data-filter-key="${f.key}">
              <span>${isAdded ? '<i class="bi bi-check-lg tmr-check-icon"></i>' : ''} ${f.label}</span>
            </li>
          `;
        }).join('')}
      </ul>
    `;

    this.openPopover(anchorEl, html, (popover) => {
      const searchInput = popover.querySelector('.tmr-popover-search');
      const items = popover.querySelectorAll('.tmr-popover-item');

      if (searchInput) {
        let popoverDebounce = null;
        searchInput.addEventListener('input', (e) => {
          clearTimeout(popoverDebounce);
          popoverDebounce = setTimeout(() => {
            const q = e.target.value.toLowerCase().trim();
            items.forEach(item => {
              const text = item.textContent.toLowerCase();
              item.style.display = text.includes(q) ? 'flex' : 'none';
            });
          }, 200);
        });
      }

      items.forEach(item => {
        item.addEventListener('click', (e) => {
          e.stopPropagation();
          const key = item.dataset.filterKey;
          const filterDef = this.getFilterDef(key);

          this.trackFilterEvent('filter_added', {
            filter_key: key,
            filter_label: filterDef ? filterDef.label : key,
          });

          // Add filter chip as unset if not already present
          if (!this.state.filters[key]) {
            this.state.filters[key] = [];
            this.renderChipRow();
            this.saveStateToStorage();
          }

          this.closePopover();

          // Auto-open chip value popover for seamless value picking
          setTimeout(() => {
            const chipEl = this.chipRow.querySelector(`[data-filter-key="${key}"] .tmr-chip-value-btn`);
            if (chipEl && filterDef) {
              this.toggleChipValuePopover(chipEl, filterDef);
            }
          }, 50);
        });
      });
    });
  }

  // --- Popover 2: Chip Value Selection ---
  async toggleChipValuePopover(anchorEl, filterDef) {
    const currentSelected = this.state.filters[filterDef.key] || [];

    const loadingHtml = `
      <div class="tmr-popover-header">
        <input type="text" class="tmr-popover-search" placeholder="Search ${filterDef.label.toLowerCase()}...">
      </div>
      <div class="p-3 text-center text-muted small"><i class="bi bi-arrow-repeat spin"></i> Loading options...</div>
    `;

    this.openPopover(anchorEl, loadingHtml);

    // Fetch options
    const options = await this.fetchFilterOptions(filterDef);

    // Multi-select menus always get a search box; single-select only when the config asks for it
    const showSearchBox = filterDef.type === 'multi_select' || filterDef.searchable === true;
    const menuHtml = `
      ${showSearchBox ? `
      <div class="tmr-popover-header">
        <input type="text" class="tmr-popover-search" placeholder="Search ${filterDef.label.toLowerCase()}...">
      </div>` : ''}
      <ul class="tmr-popover-list">
        ${options.map(opt => {
          const isChecked = currentSelected.includes(opt.value);
          return `
            <li class="tmr-popover-item ${isChecked ? 'selected' : ''}" data-value="${opt.value}">
              <span>${isChecked ? '<i class="bi bi-check-lg tmr-check-icon"></i>' : ''} ${opt.label}</span>
            </li>
          `;
        }).join('')}
      </ul>
      <div class="tmr-popover-footer">
        <button type="button" class="tmr-popover-footer-btn clear">Clear</button>
        <button type="button" class="tmr-popover-footer-btn done">Done</button>
      </div>
    `;

    this.openPopover(anchorEl, menuHtml, (popover) => {
      const searchInput = popover.querySelector('.tmr-popover-search');
      const items = popover.querySelectorAll('.tmr-popover-item');
      const clearBtn = popover.querySelector('.tmr-popover-footer-btn.clear');
      const doneBtn = popover.querySelector('.tmr-popover-footer-btn.done');

      if (searchInput) {
        let valuesDebounce = null;
        searchInput.addEventListener('input', (e) => {
          const rawVal = e.target.value;
          clearTimeout(valuesDebounce);
          valuesDebounce = setTimeout(async () => {
            const q = rawVal.trim();
            if (filterDef.source === 'dynamic' && options.length > 50) {
              // Dynamic server-side search
              const filteredOpts = await this.fetchFilterOptions(filterDef, q);
              const listEl = popover.querySelector('.tmr-popover-list');
              if (listEl) {
                listEl.innerHTML = filteredOpts.map(opt => {
                  const isChecked = (this.state.filters[filterDef.key] || []).includes(opt.value);
                  return `
                    <li class="tmr-popover-item ${isChecked ? 'selected' : ''}" data-value="${opt.value}">
                      <span>${isChecked ? '<i class="bi bi-check-lg tmr-check-icon"></i>' : ''} ${opt.label}</span>
                    </li>
                  `;
                }).join('');
                this.bindOptionItemClick(popover, filterDef);
              }
            } else {
              // Client-side search
              const query = q.toLowerCase();
              items.forEach(item => {
                const text = item.textContent.toLowerCase();
                item.style.display = text.includes(query) ? 'flex' : 'none';
              });
            }
          }, 250);
        });
      }

      this.bindOptionItemClick(popover, filterDef);

      if (clearBtn) {
        clearBtn.addEventListener('click', () => {
          this.trackFilterEvent('filter_values_cleared', { filter_key: filterDef.key });
          this.state.filters[filterDef.key] = [];
          this.renderChipRow();
          this.onStateChanged();
          this.closePopover();
        });
      }

      if (doneBtn) {
        doneBtn.addEventListener('click', () => {
          this.closePopover();
        });
      }
    });
  }

  bindOptionItemClick(popover, filterDef) {
    const items = popover.querySelectorAll('.tmr-popover-item');
    items.forEach(item => {
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        const val = item.dataset.value;
        let selected = this.state.filters[filterDef.key] || [];

        if (filterDef.type === 'single_select') {
          selected = [val];
        } else {
          if (selected.includes(val)) {
            selected = selected.filter(v => v !== val);
          } else {
            selected = [...selected, val];
          }
        }

        this.trackFilterEvent('filter_value_selected', {
          filter_key: filterDef.key,
          filter_label: filterDef.label,
          selected_count: selected.length,
          value: val,
        });

        this.state.filters[filterDef.key] = selected;
        this.renderChipRow();

        // Update option item UI
        const isChecked = selected.includes(val);
        if (filterDef.type === 'single_select') {
          items.forEach(i => i.classList.remove('selected'));
        }
        item.classList.toggle('selected', isChecked);
        const labelText = item.textContent.replace('✓', '').trim();
        item.innerHTML = `<span>${isChecked ? '<i class="bi bi-check-lg tmr-check-icon"></i>' : ''} ${labelText}</span>`;

        // Live apply changes
        this.onStateChanged();
      });
    });
  }

  async fetchFilterOptions(filterDef, query = '') {
    let opts = [];
    if (filterDef.source === 'static' && filterDef.options) {
      opts = filterDef.options.map(opt => 
        typeof opt === 'object' ? opt : { value: opt, label: opt }
      );
    } else {
      const cacheKey = `${filterDef.key}:${query}`;
      if (this.dynamicOptionsCache[cacheKey]) {
        opts = this.dynamicOptionsCache[cacheKey];
      } else {
        try {
          const resp = await fetch(`/api/filters/options/?page=${this.pageKey}&filter=${filterDef.key}&q=${encodeURIComponent(query)}`);
          if (resp.ok) {
            const data = await resp.json();
            opts = data.options || [];
            this.dynamicOptionsCache[cacheKey] = opts;
          }
        } catch (err) {
          console.error('Error fetching filter options:', err);
        }
      }
    }
    this.cacheOptionLabels(filterDef.key, opts);
    return opts;
  }

  // --- Popover 3: Time Period Menu ---
  toggleTimePopover(anchorEl) {
    const timeOptions = [
      { key: 'this_month', label: 'This month' },
      { key: 'last_month', label: 'Last month' },
      { key: 'last_3_months', label: 'Last 3 months' },
      { key: 'this_year', label: 'This year' },
      { key: 'all', label: 'All time' },
    ];

    const currentKey = this.state.time_period || 'this_month';
    const html = `
      <ul class="tmr-popover-list">
        ${timeOptions.map(opt => `
          <li class="tmr-popover-item ${opt.key === currentKey ? 'selected' : ''}" data-time-key="${opt.key}">
            <span>${opt.key === currentKey ? '<i class="bi bi-check-lg tmr-check-icon"></i>' : ''} ${opt.label}</span>
          </li>
        `).join('')}
      </ul>
    `;

    this.openPopover(anchorEl, html, (popover) => {
      popover.querySelectorAll('.tmr-popover-item').forEach(item => {
        item.addEventListener('click', (e) => {
          e.stopPropagation();
          const key = item.dataset.timeKey;
          this.trackFilterEvent('filter_time_period_selected', { time_period: key });
          this.state.time_period = key;

          const labelSpan = this.container.querySelector('.tmr-time-label');
          if (labelSpan) {
            const match = timeOptions.find(o => o.key === key);
            labelSpan.textContent = match ? match.label : 'This month';
          }

          this.closePopover();
          this.onStateChanged();
        });
      });
    });
  }

  // --- Popover 4: Sort Menu ---
  toggleSortPopover(anchorEl) {
    const sortOptions = this.config.sort_options || [
      { key: 'date_desc', label: 'Date, newest' },
      { key: 'date_asc', label: 'Date, oldest' },
      { key: 'amount_desc', label: 'Amount, highest' },
      { key: 'amount_asc', label: 'Amount, lowest' },
    ];

    const currentKey = this.state.sort || 'date_desc';
    const html = `
      <ul class="tmr-popover-list">
        ${sortOptions.map(opt => `
          <li class="tmr-popover-item ${opt.key === currentKey ? 'selected' : ''}" data-sort-key="${opt.key}">
            <span>${opt.key === currentKey ? '<i class="bi bi-check-lg tmr-check-icon"></i>' : ''} ${opt.label}</span>
          </li>
        `).join('')}
      </ul>
    `;

    this.openPopover(anchorEl, html, (popover) => {
      popover.querySelectorAll('.tmr-popover-item').forEach(item => {
        item.addEventListener('click', (e) => {
          e.stopPropagation();
          const key = item.dataset.sortKey;
          this.trackFilterEvent('filter_sort_selected', { sort: key });
          this.state.sort = key;

          const labelSpan = this.container.querySelector('.tmr-sort-label');
          if (labelSpan) {
            const match = sortOptions.find(o => o.key === key);
            labelSpan.textContent = match ? match.label : 'Date, newest';
          }

          this.closePopover();
          this.onStateChanged();
        });
      });
    });
  }

  onStateChanged() {
    this.saveStateToStorage();
    this.applyStateToURLAndFetch(true);
  }

  buildQueryString() {
    const params = new URLSearchParams();

    if (this.state.search) {
      params.set('search', this.state.search);
    }
    if (this.config.supports_time_period !== false && this.state.time_period && this.state.time_period !== (this.config.default_time_range || 'this_month')) {
      params.set('time_period', this.state.time_period);
    }
    if (this.state.start_date) params.set('start_date', this.state.start_date);
    if (this.state.end_date) params.set('end_date', this.state.end_date);
    if (this.state.sort && this.state.sort !== (this.config.default_sort || 'date_desc')) {
      params.set('sort', this.state.sort);
    }

    if (this.state.filters) {
      for (const [key, vals] of Object.entries(this.state.filters)) {
        if (Array.isArray(vals)) {
          vals.forEach(v => {
            if (v && String(v).trim()) {
              params.append(key, v);
            }
          });
        }
      }
    }
    return params.toString();
  }

  applyStateToURLAndFetch(triggerFetch = true) {
    const queryString = this.buildQueryString();
    const newRelativePathQuery = window.location.pathname + (queryString ? '?' + queryString : '');
    window.history.replaceState(null, '', newRelativePathQuery);

    if (triggerFetch) {
      const activeFilterKeys = Object.keys(this.state.filters || {}).filter(k => (this.state.filters[k] || []).length > 0);
      const hasSearch = !!(this.state.search && this.state.search.trim());
      const hasTime = this.config.supports_time_period !== false &&
        this.state.time_period &&
        this.state.time_period !== (this.config.default_time_range || 'this_month');
      const hasSort = !!(this.state.sort && this.state.sort !== (this.config.default_sort || 'date_desc'));

      this.trackFilterEvent('filter_applied', {
        has_search: hasSearch,
        time_period: this.state.time_period || 'this_month',
        is_custom_time: hasTime,
        active_filters: activeFilterKeys,
        active_filters_count: activeFilterKeys.length,
        sort: this.state.sort || 'date_desc',
        is_custom_sort: hasSort,
        total_active_criteria: activeFilterKeys.length + (hasSearch ? 1 : 0) + (hasTime ? 1 : 0) + (hasSort ? 1 : 0),
      });

      // HTMX / AJAX refresh shell
      const shell = document.getElementById(`${this.pageKey}-list-shell`) || 
                    document.getElementById(`${this.pageKey}-shell`) || 
                    document.getElementById(`${this.pageKey.replace(/s$/, '')}-list-shell`) ||
                    document.getElementById(`${this.pageKey.replace(/s$/, '')}-detail-shell`) ||
                    document.getElementById('account-list-shell') || 
                    document.getElementById('account-detail-shell') || 
                    document.getElementById('capital-event-list-shell') || 
                    document.getElementById('recurring-list-shell') || 
                    document.getElementById('expense-list-shell') || 
                    document.getElementById('income-list-shell');
      if (shell && window.htmx) {
        window.htmx.ajax('GET', newRelativePathQuery, {
          target: shell,
          swap: 'outerHTML show:none',
          indicator: '#global-progress-bar'
        });
      } else {
        // Fallback page navigation if HTMX unavailable
        window.location.href = newRelativePathQuery;
      }
    }
  }
}

// Auto-initialize filter systems on page load, and re-initialize whenever htmx swaps
// actually replace a toolbar's own DOM (e.g. after applying a filter), so chips reflect
// the applied state immediately instead of only after a full page reload.
window.__tmrFilterInstances = window.__tmrFilterInstances || {};
window.__tmrFilterContainers = window.__tmrFilterContainers || {};

function initTMRFilterToolbar(container) {
  const pageKey = container.dataset.pageKey;
  if (!pageKey) return;

  // htmx can fire afterSettle more than once for a single swap; skip re-init if this
  // exact DOM node is already the active one, otherwise buttons end up with duplicate
  // listeners (stray popovers that never close on outside click).
  if (window.__tmrFilterContainers[pageKey] === container) return;

  const existing = window.__tmrFilterInstances[pageKey];
  if (existing && typeof existing.destroy === 'function') {
    existing.destroy();
  }

  window.__tmrFilterContainers[pageKey] = container;
  window.__tmrFilterInstances[pageKey] = new TMRFilterSystem(pageKey);
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.tmr-toolbar-container[data-page-key]').forEach(initTMRFilterToolbar);
});

// Only reinit toolbars whose DOM was actually part of THIS swap. Re-running for every
// unrelated htmx swap on the page would rebind listeners onto buttons that were never
// replaced, stacking duplicate handlers and leaving orphaned, un-closeable popovers.
//
// NOTE: for "outerHTML" swaps, evt.detail.target still refers to the OLD (now-detached)
// element that was replaced, not the freshly-inserted one - using it here would silently
// no-op forever. evt.target (the native event dispatch target) is the actually-connected,
// newly-settled element, so that's what we use to find the toolbar to (re)initialize.
document.body.addEventListener('htmx:afterSettle', function (evt) {
  const swappedEl = evt.target;
  if (!swappedEl) return;

  if (swappedEl.matches && swappedEl.matches('.tmr-toolbar-container[data-page-key]')) {
    initTMRFilterToolbar(swappedEl);
  } else if (swappedEl.querySelectorAll) {
    swappedEl.querySelectorAll('.tmr-toolbar-container[data-page-key]').forEach(initTMRFilterToolbar);
  }
});
