function tmrFlows(initialCategory, initialPill) {
  return {
    activeCategory: initialCategory,
    activePill: initialPill,
    addonStates: {},

    switchCategory(cat, firstPill) {
      this.activeCategory = cat;
      this.activePill = firstPill;
    },

    toggleAddon(key) {
      this.addonStates[key] = !this.addonStates[key];
    },
  };
}

function tmrFlowWizard(totalSteps, initialStep = 1) {
  return {
    currentStep: initialStep,
    totalSteps,

    totalPhases() {
      return this.totalSteps + 1;
    },

    isReviewStep() {
      return this.currentStep === this.totalPhases();
    },

    goToStep(targetStep) {
      if (targetStep >= 1 && targetStep <= this.totalPhases()) {
        this.currentStep = targetStep;
      }
    },

    stepTabClass(stepNumber) {
      return {
        active: this.currentStep === stepNumber,
        completed: this.currentStep > stepNumber,
      };
    },

    progressWidth() {
      return `${Math.round((this.currentStep / this.totalPhases()) * 100)}%`;
    },

    stepCounterLabel() {
      return `Step ${this.currentStep} of ${this.totalPhases()}`;
    },
  };
}

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

  const icon = btn.querySelector('i');
  if (icon) {
    icon.classList.remove('bi-check2-circle');
    icon.classList.add('bi-check-circle-fill');
  }
});
