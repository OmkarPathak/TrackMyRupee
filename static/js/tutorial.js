// Initialize driver with fallback for different build versions
const driver = window.driver?.js?.driver || window.driver;

const desktopSteps = [
    { element: '#tour-income-card', popover: { title: gettext('Financial Overview'), description: gettext('Track your Income and Expenses here to see your monthly growth.'), side: "bottom", align: 'start' } },
    { element: '#tour-budget-card', popover: { title: gettext('Budget Limits'), description: gettext('Visual bars help you stay within your category limits.'), side: "top", align: 'start' } },
    { element: '#nav-expenses', popover: { title: gettext('Transaction List'), description: gettext('View and manage your detailed transaction history here.'), side: "bottom", align: 'start' } },
    { element: '#global-fab', popover: { title: gettext('Quick Actions'), description: gettext('Click (+) to instantly add a new Income or Expense.'), side: "top", align: 'end' } },
];

const mobileSteps = [
    { element: '.navbar-toggler', popover: { title: gettext('Menu & Settings'), description: gettext('Access your Settings, Theme, and other options here.'), side: "bottom", align: 'end' } },
    { element: '#tour-income-card', popover: { title: gettext('Income'), description: gettext('Track your total monthly income.'), side: "bottom", align: 'center' } },
    { element: '#tour-expenses-card', popover: { title: gettext('Expenses'), description: gettext('Monitor your total monthly spending.'), side: "bottom", align: 'center' } },
    { element: '#global-fab', popover: { title: gettext('Add New'), description: gettext('Tap (+) to record a transaction.'), side: "top", align: 'end' } },
];

const isMobile = window.innerWidth < 768; // Bootstrap md breakpoint

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
        steps = isMobile ? mobileSteps : desktopSteps;
    }
    
    console.log('Tour Steps:', steps);
    
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
