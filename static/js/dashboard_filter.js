/**
 * Dashboard Date Range Picker
 * Handles presets, interactive calendar, and form synchronization.
 */

class DashboardDatePicker {
    constructor() {
        this.trigger = document.getElementById('date-range-trigger');
        this.startInput = document.getElementById('start_date');
        this.endInput = document.getElementById('end_date');
        this.visualStart = document.getElementById('visual-start-date');
        this.visualEnd = document.getElementById('visual-end-date');
        this.presets = document.querySelectorAll('.preset-item');
        this.applyBtn = document.getElementById('apply-filter-btn');
        this.cancelBtn = document.getElementById('cancel-filter-btn');
        this.form = document.getElementById('dashboard-home-filter-form') || document.getElementById('dashboard-mobile-filter-form');
        
        this.init();
    }

    init() {
        // Safe check: only init if all required elements for the modernized picker exist
        if (!this.trigger || !this.startInput || !this.endInput || !this.form || !this.applyBtn) {
            return;
        }

        // Initialize Flatpickr
        this.fp = flatpickr("#inline-calendar", {
            inline: true,
            mode: "range",
            dateFormat: "Y-m-d",
            showMonths: 1, // Changed to 1 to ensure it fits consistently
            monthSelectorType: "static",
            onChange: (selectedDates) => {
                this.handleCalendarChange(selectedDates);
            }
        });

        // Set initial values from hidden inputs
        if (this.startInput.value && this.endInput.value) {
            this.fp.setDate([this.startInput.value, this.endInput.value]);
            this.updateVisualInputs(this.startInput.value, this.endInput.value);
            this.updateTriggerLabel(this.startInput.value, this.endInput.value);
        }

        // Event Listeners
        this.presets.forEach(item => {
            item.addEventListener('click', (e) => this.handlePresetClick(e));
        });

        if (this.applyBtn) {
            this.applyBtn.addEventListener('click', () => this.apply());
        }
        if (this.cancelBtn) {
            this.cancelBtn.addEventListener('click', () => this.close());
        }
    }

    handlePresetClick(e) {
        const preset = e.currentTarget.dataset.preset;
        const range = this.calculateRange(preset);
        
        // Update selection state
        this.presets.forEach(p => p.classList.remove('active'));
        e.currentTarget.classList.add('active');

        if (range) {
            this.fp.setDate([range.start, range.end]);
            this.updateVisualInputs(range.start, range.end);
        }
    }

    handleCalendarChange(selectedDates) {
        // Clear active preset when manual selection happens
        if (selectedDates.length === 2) {
            const start = this.formatDate(selectedDates[0]);
            const end = this.formatDate(selectedDates[1]);
            this.updateVisualInputs(start, end);
            this.updateTriggerLabel(start, end);
        }
    }

    calculateRange(preset) {
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        
        let start, end = new Date(today);

        switch (preset) {
            case 'today':
                start = new Date(today);
                break;
            case 'yesterday':
                start = new Date(today);
                start.setDate(today.getDate() - 1);
                end = new Date(start);
                break;
            case 'last-7-days':
                start = new Date(today);
                start.setDate(today.getDate() - 6);
                break;
            case 'last-30-days':
                start = new Date(today);
                start.setDate(today.getDate() - 29);
                break;
            case 'this-month':
                start = new Date(today.getFullYear(), today.getMonth(), 1);
                break;
            case 'last-month':
                start = new Date(today.getFullYear(), today.getMonth() - 1, 1);
                end = new Date(today.getFullYear(), today.getMonth(), 0);
                break;
            case 'last-90-days':
                start = new Date(today);
                start.setDate(today.getDate() - 89);
                break;
            case 'this-year':
                start = new Date(today.getFullYear(), 0, 1);
                break;
            case 'quarter-to-date':
                const quarter = Math.floor(today.getMonth() / 3);
                start = new Date(today.getFullYear(), quarter * 3, 1);
                break;
            default:
                return null;
        }

        return {
            start: this.formatDate(start),
            end: this.formatDate(end)
        };
    }

    formatDate(date) {
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    updateVisualInputs(start, end) {
        if (this.visualStart) this.visualStart.value = start;
        if (this.visualEnd) this.visualEnd.value = end;
    }

    updateTriggerLabel(start, end) {
        if (!this.trigger) return;
        
        const opt = { month: 'short', day: 'numeric', year: 'numeric' };
        const d1 = new Date(start).toLocaleDateString(undefined, opt);
        const d2 = new Date(end).toLocaleDateString(undefined, opt);
        
        const labelText = document.getElementById('active-range-text');
        if (labelText) {
            labelText.innerText = `${d1} - ${d2}`;
        }
    }

    apply() {
        if (!this.fp || !this.form) return;
        const range = this.fp.selectedDates;
        if (range.length === 2) {
            if (this.startInput) this.startInput.value = this.formatDate(range[0]);
            if (this.endInput) this.endInput.value = this.formatDate(range[1]);
            
            if (window.showLoader) window.showLoader();
            
            // Context-aware form finding is more robust than ID lookups
            const formToSubmit = this.applyBtn ? this.applyBtn.closest('form') : this.form;
            if (formToSubmit) {
                formToSubmit.submit();
            } else {
                console.error("DashboardDatePicker: No form found to submit");
            }
        }
    }

    close() {
        // Dropdowns close automatically in Bootstrap, but we can force it if needed
        const dropdown = bootstrap.Dropdown.getInstance(this.trigger);
        if (dropdown) dropdown.hide();
    }
}

// Initialize on load
document.addEventListener('DOMContentLoaded', () => {
    window.dashboardDatePicker = new DashboardDatePicker();
});
