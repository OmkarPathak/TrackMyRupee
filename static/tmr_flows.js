function tmrFlows(initialCategory = 'all', initialFlowKey = 'loan', flowsData = null) {
  let flows = flowsData;
  if (!Array.isArray(flows) || !flows.length) {
    const el = document.getElementById('tmr-flows-data');
    if (el && el.textContent) {
      try {
        flows = JSON.parse(el.textContent);
      } catch (e) {
        flows = [];
      }
    } else {
      flows = [];
    }
  }

  return {
    activeCategory: initialCategory,
    selectedFlowKey: initialFlowKey,
    showAllChecklist: false,
    flows: flows,
    addonStates: {},

    switchCategory(cat) {
      this.activeCategory = cat;
    },

    selectFlow(key) {
      this.selectedFlowKey = key;
    },

    selectedFlow() {
      return this.flows.find(f => f.key === this.selectedFlowKey) || this.flows[0] || {};
    },

    matchesCategory(flowCat) {
      return this.activeCategory === 'all' || this.activeCategory === flowCat;
    },

    configuredCountInActiveCategory() {
      return this.flows.filter(f => f.is_configured && this.matchesCategory(f.category)).length;
    },

    hasConfiguredInActiveCategory() {
      return this.configuredCountInActiveCategory() > 0;
    },

    toggleAddon(key) {
      this.addonStates[key] = !this.addonStates[key];
    },
  };
}
window.tmrFlows = tmrFlows;



function tmrFlowWizard(totalSteps, initialStep = 1, initialHasWarnings = false) {
  return {
    currentStep: initialStep,
    totalSteps,
    hasWarnings: Boolean(initialHasWarnings),
    instrumentType: 'SIP',
    interestPayout: '',
    isFinanced: false,
    createRepaymentSchedule: true,
    midTenure: false,
    includeDownPayment: false,
    // True from the moment the review step is opened until the server's answer for the form as it is now has been
    // swapped in: the review box holds the previous answer until then, so it is shown faded and Confirm is held.
    reviewLoading: false,
    reviewTimer: null,
    freshRequests: new WeakSet(),

    init() {
      this.setKeyboards();
      this.syncFormState();
      this.checkWarnings();
      this.watchReviewRequests();

      const form = document.querySelector('form[hx-post]');
      if (form) {
        form.addEventListener('change', () => this.syncFormState());
        form.addEventListener('input', () => this.syncFormState());
      }

      document.addEventListener('htmx:afterSwap', () => {
        this.checkWarnings();
      });

      // Confirm found a problem on a step that is not showing: go back to the first one that has it
      document.addEventListener('htmx:afterSettle', (event) => {
        const path = (event.detail && event.detail.pathInfo && event.detail.pathInfo.requestPath) || '';
        if (/\/commit\/?$/.test(path)) this.goToFirstError();
      });
    },

    goToFirstError() {
      if (!this.isReviewStep()) return;
      const review = this.reviewStepNumber();
      for (let number = 1; number < review; number++) {
        const stepEl = document.querySelector(`.wizard-step-content[data-step="${number}"]`);
        if (!stepEl) continue;
        const spot = Array.from(stepEl.querySelectorAll('[id^="field-errors-"]')).find((el) => el.textContent.trim());
        if (!spot) continue;
        this.currentStep = number;
        this.$nextTick(() => spot.scrollIntoView({ behavior: 'smooth', block: 'center' }));
        return;
      }
    },

    setKeyboards() {
      // A phone shows its number pad for a number field, but the plain one has punctuation and letters a rate or a
      // tenure never needs: a whole number (tenure, day of month) gets the digit pad, a rate the one with a point.
      // The amounts (data-money) are set by the money input itself.
      document.querySelectorAll('form[hx-post] input[type="number"]:not([data-money])').forEach((input) => {
        if (input.hasAttribute('inputmode')) return;
        const step = input.getAttribute('step');
        input.setAttribute('inputmode', !step || step === '1' ? 'numeric' : 'decimal');
      });
    },

    watchReviewRequests() {
      // A request that was already on its way when the review opened carries the old form: only the ones sent after
      // count (htmx sends the one queued behind it, with the current values, as soon as it has finished).
      document.addEventListener('htmx:beforeRequest', (event) => {
        if (this.reviewLoading && event.detail && event.detail.xhr) this.freshRequests.add(event.detail.xhr);
      });
      const finished = (event) => {
        if (this.reviewLoading && event.detail && event.detail.xhr && this.freshRequests.has(event.detail.xhr)) {
          this.endReviewLoading();
        }
      };
      ['htmx:afterSwap', 'htmx:responseError', 'htmx:sendError', 'htmx:timeout', 'htmx:swapError'].forEach((name) => {
        document.addEventListener(name, finished);
      });
    },

    startReviewLoading() {
      this.reviewLoading = true;
      clearTimeout(this.reviewTimer);
      this.reviewTimer = setTimeout(() => this.endReviewLoading(), 10000);   // never leave Confirm held
    },

    endReviewLoading() {
      this.reviewLoading = false;
      clearTimeout(this.reviewTimer);
    },

    syncFormState() {
      const instInput = document.querySelector('[name="instrument_type"]');
      if (instInput) {
        this.instrumentType = instInput.value || 'SIP';
      }
      const payoutInput = document.querySelector('[name="interest_payout"]');
      if (payoutInput) {
        this.interestPayout = payoutInput.value || '';
      }
      const finInput = document.querySelector('[name="financed"]');
      if (finInput) {
        this.isFinanced = finInput.checked;
      }
      const repInput = document.querySelector('[name="create_repayment_schedule"]');
      if (repInput) {
        this.createRepaymentSchedule = repInput.checked;
      }
      const midInput = document.querySelector('[name="mid_tenure"]');
      if (midInput) {
        this.midTenure = midInput.checked;
      }
      const downInput = document.querySelector('[name="include_down_payment"]');
      if (downInput) {
        this.includeDownPayment = downInput.checked;
      }
    },

    checkWarnings() {
      const reviewEl = document.querySelector('[id^="tmr-review-"]');
      this.hasWarnings = !!(reviewEl && reviewEl.querySelector('.tmr-limit-warning'));
    },

    isStepVisible(stepKey) {
      if (stepKey === 'deposit_rd_details') {
        return this.instrumentType === 'RD';
      }
      if (stepKey === 'sip_funds') {
        return this.instrumentType === 'SIP';
      }
      if (stepKey === 'fd_rd') {
        return this.interestPayout === 'MONTHLY';      // (an RD is funded monthly: only monthly payouts can feed one)
      }
      return true;
    },

    reviewStepNumber() {
      return this.totalSteps + 1;
    },

    isReviewStep() {
      return this.currentStep === this.reviewStepNumber();
    },

    totalPhases() {
      return this.totalSteps + 1;
    },

    visibleStepsCount() {
      let count = 0;
      for (let i = 1; i <= this.totalSteps; i++) {
        const stepEl = document.querySelector(`.wizard-step-content[data-step="${i}"]`);
        const stepKey = stepEl ? stepEl.dataset.stepKey : null;
        if (!stepKey || this.isStepVisible(stepKey)) {
          count++;
        }
      }
      return count + 1; // plus Review step
    },

    currentVisibleStepNumber() {
      if (this.isReviewStep()) {
        return this.visibleStepsCount();
      }
      let num = 0;
      for (let i = 1; i <= this.currentStep; i++) {
        const stepEl = document.querySelector(`.wizard-step-content[data-step="${i}"]`);
        const stepKey = stepEl ? stepEl.dataset.stepKey : null;
        if (!stepKey || this.isStepVisible(stepKey)) {
          num++;
        }
      }
      return Math.max(num, 1);
    },

    validateCurrentStep() {
      const stepEl = document.querySelector(`.wizard-step-content[data-step="${this.currentStep}"]`);
      if (!stepEl) return true;
      const stepKey = stepEl.dataset.stepKey;
      if (stepKey && !this.isStepVisible(stepKey)) return true;

      const inputs = stepEl.querySelectorAll('input:not([type="hidden"]), select, textarea');
      let isValid = true;
      for (const input of inputs) {
        if (input.offsetWidth > 0 || input.offsetHeight > 0 || input.getClientRects().length > 0) {
          if (!input.checkValidity()) {
            isValid = false;
            input.classList.add('is-invalid');
            input.reportValidity();
            break;
          } else {
            input.classList.remove('is-invalid');
          }
        }
      }
      return isValid;
    },

    nextStep(direction = 1) {
      let target = this.currentStep + direction;
      while (target >= 1 && target <= this.reviewStepNumber()) {
        if (target === this.reviewStepNumber()) {
          this.goToStep(target);
          return;
        }
        const stepEl = document.querySelector(`.wizard-step-content[data-step="${target}"]`);
        const stepKey = stepEl ? stepEl.dataset.stepKey : null;
        if (!stepKey || this.isStepVisible(stepKey)) {
          this.goToStep(target);
          return;
        }
        target += direction;
      }
    },

    goToStep(targetStep) {
      if (targetStep < 1 || targetStep > this.reviewStepNumber()) return;
      if (targetStep > this.currentStep) {
        if (!this.validateCurrentStep()) return;
      }
      this.currentStep = targetStep;
      if (this.isReviewStep()) {
        this.checkWarnings();
        const form = document.querySelector('form[hx-post]');
        if (form && window.htmx) {
          this.startReviewLoading();
          window.htmx.trigger(form, 'tmr-preview');
        }
      }
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },

    stepTabClass(stepNumber) {
      return {
        active: this.currentStep === stepNumber,
        completed: this.currentStep > stepNumber,
      };
    },

    progressWidth() {
      return `${Math.round((this.currentVisibleStepNumber() / this.visibleStepsCount()) * 100)}%`;
    },

    // The number written in a step's circle on a phone: its place among the steps that are showing
    visibleOrdinal(stepNumber) {
      let count = 0;
      for (let i = 1; i <= stepNumber; i++) {
        const stepEl = document.querySelector(`.wizard-step-content[data-step="${i}"]`);
        const stepKey = stepEl ? stepEl.dataset.stepKey : null;
        if (!stepKey || this.isStepVisible(stepKey)) count++;
      }
      return count;
    },

    // The name of the current step (the circles carry it as data-title)
    stepTitle() {
      const dot = document.querySelector(`.wizard-dot[data-step="${this.currentStep}"]`);
      return dot ? dot.dataset.title : '';
    },

    stepCounterLabel() {
      return `Step ${this.currentVisibleStepNumber()} of ${this.visibleStepsCount()}`;
    },
  };
}
window.tmrFlowWizard = tmrFlowWizard;

function tmrCountUp(el, toValue, duration = 450) {
  const from = parseFloat(el.dataset.countFrom || '0');
  const target = parseFloat(toValue || '0');
  const start = performance.now();
  const easeOut = (t) => 1 - Math.pow(1 - t, 3);

  function step(now) {
    const t = Math.min((now - start) / duration, 1);
    el.textContent = Math.round(from + (target - from) * easeOut(t)).toLocaleString('en-IN');
    if (t < 1) {
      requestAnimationFrame(step);
    } else {
      el.dataset.countFrom = String(target);
    }
  }

  requestAnimationFrame(step);
}

document.addEventListener('htmx:afterSwap', (event) => {
  event.target.querySelectorAll('[data-count-up]').forEach((el) => {
    tmrCountUp(el, el.dataset.value || '0');
  });
});

document.addEventListener('htmx:beforeRequest', (event) => {
  const btn = event.target.matches?.('[data-confirm-btn]')
    ? event.target
    : event.target.querySelector?.('[data-confirm-btn]');
  if (!btn) {
    return;
  }

  if (btn.disabled || btn.classList.contains('disabled')) {
    event.preventDefault();
    return;
  }

  const icon = btn.querySelector('i');
  if (icon) {
    icon.classList.remove('bi-check2-circle');
    icon.classList.add('bi-check-circle-fill');
  }
});

// A Confirm that found a problem (or could not be sent) leaves the button where it was: put its icon back
['htmx:afterRequest', 'htmx:sendError', 'htmx:timeout'].forEach((name) => {
  document.addEventListener(name, (event) => {
    const btn = event.target && event.target.matches && event.target.matches('[data-confirm-btn]') ? event.target : null;
    const icon = btn && btn.querySelector('i');
    if (icon) icon.classList.remove('bi-check-circle-fill');
  });
});
