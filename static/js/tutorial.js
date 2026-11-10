// Initialize driver with fallback for different build versions
const driver = window.driver?.js?.driver || window.driver;

// The app tour comes in two forms, because the two layouts navigate differently: from 992px up the sidebar and the
// Add menu are on screen; below that there is the bottom bar with the + button and More. A step with no element is
// shown centred. Keep each tour short: where things are, not every button.
const WELCOME_STEP = {
    popover: {
        title: gettext('Welcome to TrackMyRupee'),
        description: gettext('Here is a quick tour of where things are. It takes under a minute, and you can replay it any time from Settings.'),
    },
};

const desktopSteps = [
    WELCOME_STEP,
    { element: '#tour-add-menu', popover: { title: gettext('Add anything from here'), description: gettext('Log an expense or income, move money between accounts, or record an EMI. Most days you will start with this button.'), side: "bottom", align: 'end' } },
    { element: '#tour-nav-transactions', popover: { title: gettext('Everything you log'), description: gettext('Every expense, income and transfer is listed here, with search and filters.'), side: "right", align: 'center' } },
    { element: '#tour-nav-accounts', popover: { title: gettext('Your accounts'), description: gettext('Add your bank accounts, cards, loans and investments to see your balances and net worth.'), side: "right", align: 'center' } },
    { element: '#tour-nav-analytics', popover: { title: gettext('Review your month'), description: gettext('Analytics starts with a plain sentence about your spending, then shows the charts behind it.'), side: "right", align: 'center' } },
    { element: '#tour-nav-guided-setup', popover: { title: gettext('Guided Setup'), description: gettext('Took a loan or started a new job? Tell it what happened and it sets everything up in one short form.'), side: "right", align: 'center' } },
    { element: '#sidebarSearchTrigger', popover: { title: gettext('Find any page'), description: gettext('Not sure where something is? Search for it here, or press Ctrl K (Cmd K on a Mac).'), side: "right", align: 'start' } },
    { element: '#tour-nav-settings', popover: { title: gettext('Settings and help'), description: gettext('Change your currency, language and theme, or replay this tour.'), side: "right", align: 'center' } },
];

const mobileSteps = [
    WELCOME_STEP,
    { element: '#global-fab', popover: { title: gettext('Tap + to add'), description: gettext('Add an expense in a few seconds. The + also moves money between accounts and records an EMI.'), side: "top", align: 'end' } },
    { element: '.mobile-nav-capsule', popover: { title: gettext('Your three main screens'), description: gettext('Dashboard is your summary, Transactions lists everything you have logged, and Accounts shows your balances.'), side: "top", align: 'center' } },
    { element: '.mobile-nav-item-more', popover: { title: gettext('Everything else is in More'), description: gettext('Budgets, bills, analytics, loans, goals and settings are all in this menu. You can replay this tour from Settings.'), side: "top", align: 'end' } },
];

// The sidebar is only on screen from the lg breakpoint (992px) up
const usesSidebar = () => window.matchMedia('(min-width: 992px)').matches;
const isOnScreen = (el) => !!el && el.getClientRects().length > 0;

const onboardingSteps = [
    { element: '#hero-card', popover: { title: gettext('Welcome Aboard! 🚀'), description: gettext('This is your starting point. Since you are new, we have simplified things for you.'), side: "top", align: 'center' } },
    { element: '#hero-income-btn', popover: { title: gettext('Record Income'), description: gettext('Got your salary? Click here to add your first income entry.'), side: "bottom", align: 'start' } },
    { element: '#hero-expense-btn', popover: { title: gettext('Add Expense'), description: gettext('Spent money on coffee? Click here to log your first expense.'), side: "bottom", align: 'end' } },
];

// Global instance to hold the driver
let tourDriver;

function startTour(mode = 'standard') {
    console.log('Starting Tour... Mode:', mode);
    
    // Lazy resolve the driver library
    const driverLib = window.driver?.js?.driver || window.driver;
    if (!driverLib) {
        console.error('Driver.js library not found! Is the CDN script loaded?');
        return;
    }

    // Select steps based on mode
    let steps;
    if (mode === 'onboarding') {
        steps = onboardingSteps;
    } else {
        steps = usesSidebar() ? desktopSteps : mobileSteps;
    }
    // Copy, so the Install step added below is not added twice if the tour is started again; and skip any step whose
    // target is not on screen (a collapsed sidebar, an unusual layout) rather than point at nothing.
    steps = steps.filter((step) => !step.element || isOnScreen(document.querySelector(step.element)));
    
    
    // Verify elements exist (Debug)
    steps.forEach((step, index) => {
        const el = document.querySelector(step.element);
        if (!el) console.warn(`Step ${index + 1} element not found:`, step.element);
    });

    // Check if Install App button is visible and add step dynamically
    const installBtn = document.getElementById('installAppBtn');
    const installContainer = document.getElementById('installAppContainer');
    
    // Check if container is visible (style.display not none)
    if (installContainer && installContainer.offsetWidth > 0 && installContainer.offsetHeight > 0) {
        steps.push({ 
            element: '#installAppBtn', 
            popover: { 
                title: gettext('Install App 📲'), 
                description: gettext('Add TrackMyRupee to your home screen for a fast, native app experience.'), 
                side: "bottom", 
                align: 'end' 
            } 
        });
    }

    // Destroy existing driver if it exists to clean up
    if (tourDriver) {
        tourDriver.destroy();
    }

    // Instantiate NEW driver with the chosen steps
    tourDriver = driverLib({
        animate: true,
        showProgress: true,
        nextBtnText: gettext('Next'),
        prevBtnText: gettext('Back'),
        doneBtnText: gettext('Done'),
        progressText: gettext('{{current}} of {{total}}'),
        steps: steps, // Pass steps directly here
        onDestroyStarted: () => {
            markTutorialComplete();
            if (tourDriver) {
                tourDriver.destroy();
                tourDriver = null;
            }
        },
    });

    tourDriver.drive();
}

function markTutorialComplete() {
    // Only mark complete if we are logged in (indicated by the presence of the tutorial complete URL)
    if (window.tutorialCompleteUrl) {
        fetch(window.tutorialCompleteUrl, {
            method: 'POST',
            headers: {
                'X-CSRFToken': getCookie('csrftoken'),
                'Content-Type': 'application/json'
            }
        }).then(response => {
            if (response.ok) {
                console.log('Tutorial marked as complete');
            }
        }).catch(err => console.error(err));
    }
}

// CSRF Token Helper
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}
