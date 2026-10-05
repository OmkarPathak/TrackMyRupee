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
