"""Content of the public Features page.

Every claim here must be backed by the user guide (guide-src). Keep it factual: the app does no bank sync, no SMS
reading and no external AI, and the page must not suggest otherwise.
"""
from django.utils.translation import gettext as _


# Decorative icon per category and per card, in card order: names of the symbols in templates/partials/features_icons.html.
ICONS = {
    'tracking': ('chat', ['chat', 'sparkle', 'repeat', 'calendar', 'search', 'upload']),
    'accounts': ('bank', ['rupee', 'wallet', 'trend-up', 'card', 'check', 'shield']),
    'investments': ('bars', ['layers', 'swap', 'link', 'clock', 'blocks', 'coins']),
    'planning': ('calendar', ['repeat', 'sliders', 'flag', 'home', 'gift']),
    'flows': ('compass', ['wand', 'map', 'lifebuoy']),
    'family': ('people', ['people', 'layers', 'key', 'eye', 'lifebuoy', 'leaf', 'receipt']),
    'noticing': ('sparkle', ['eye', 'hash', 'leaf', 'calendar', 'sliders', 'compass', 'lock']),
    'insights': ('bulb', ['bars', 'check', 'trend-down', 'gift']),
    'alerts': ('bell', ['clock', 'moon']),
    'trust': ('lock', ['eye-off', 'scales', 'trash', 'unlock']),
    'everywhere': ('globe', ['language', 'phone', 'currency', 'theme', 'play']),
}


def feature_categories():
    categories = _feature_categories()
    for category in categories:
        category_icon, item_icons = ICONS[category['id']]
        assert len(item_icons) == len(category['items']), category['id']
        category['icon'] = category_icon
        for item, icon in zip(category['items'], item_icons):
            item['icon'] = icon
    return categories


def _feature_categories():
    return [
        {
            'id': 'tracking',
            'title': _('Log money in seconds'),
            'description': _('Type it the way you would text a friend. No bank login, no SMS access.'),
            'items': [
                {
                    'title': _('Type it like a message'),
                    'desc': _('Write "swiggy 320", "uber to airport 450 upi yesterday" or "laptop bag 2 lakh". It understands amounts like 1.5k, lakh and crore, dates, UPI, cards, your account names, and Hindi and Marathi. You check it, then add it. You can also speak it.'),
                },
                {
                    'title': _('Categories that learn your habits'),
                    'desc': _('Your own history and common words like Swiggy, Uber and petrol suggest the category, and your "usual" expenses are one tap away. It all runs inside the app, and your descriptions are not sent to any third-party service.'),
                },
                {
                    'title': _('Expenses, income and transfers'),
                    'desc': _('Log what you spend and what you earn, and move money between your own accounts without it counting as spending. Cashback and refunds are kept apart from income, so your savings rate stays honest.'),
                },
                {
                    'title': _('Your month starts on salary day'),
                    'desc': _('Set the day your salary lands and every total, budget and chart follows your real cycle instead of the calendar month.'),
                },
                {
                    'title': _('Find anything, fast'),
                    'desc': _('Filter by period, category, account and payment method, search by description, and sort. The totals update as you filter. A calendar shows every day at a glance, with salary days and upcoming payments marked.'),
                },
                {
                    'title': _('Bring your data in, take it out'),
                    'desc': _('Import an Excel or CSV file: columns are detected for you, duplicate rows are skipped and categories are suggested. Export everything to CSV whenever you like, on every plan.'),
                },
            ],
        },
        {
            'id': 'accounts',
            'title': _('Your whole balance sheet'),
            'description': _('Every account that matters to an Indian household, in one true net worth.'),
            'items': [
                {
                    'title': _('Built for Indian accounts'),
                    'desc': _('Savings, salary and cash, fixed and recurring deposits, PPF, EPF and NPS, mutual funds, demat, gold and sovereign gold bonds, credit cards, BNPL, overdraft, loans, real estate, vehicles, and life and health insurance.'),
                },
                {
                    'title': _('One real net worth'),
                    'desc': _('Assets minus what you owe, as one number with its history. Foreign-currency balances are converted at the day\'s rate, and a past date uses the rate from that date.'),
                },
                {
                    'title': _('Deposits that accrue like your bank\'s'),
                    'desc': _('Fixed and recurring deposits earn interest the way a bank counts it, with each recurring installment accruing from its own date, up to the maturity amount your bank quotes.'),
                },
                {
                    'title': _('Credit cards without surprises'),
                    'desc': _('See the limit, the credit still available and the next billing date for each card, BNPL and overdraft account, with a reminder three days before the bill.'),
                },
                {
                    'title': _('Reconcile against your statement'),
                    'desc': _('Enter the balance on your bank or card statement. The app shows the difference, points at the likely causes, and can post the adjustment for you.'),
                },
                {
                    'title': _('Insurance and big assets'),
                    'desc': _('Track life and health policies with sum assured, premiums and next due date. A savings policy counts the premiums paid so far, while pure protection such as term and health cover stays out of your net worth. Property and vehicle values can be updated as they change.'),
                },
            ],
        },
        {
            'id': 'investments',
            'title': _('Investments, fund by fund'),
            'description': _('See which fund every rupee went to, priced the way the market prices it.'),
            'items': [
                {
                    'title': _('Holdings inside each account'),
                    'desc': _('Track units, average cost, current value and gain or loss for every fund, stock, ETF and gold holding, with a full purchase and sale history you can edit.'),
                },
                {
                    'title': _('Daily mutual fund NAVs'),
                    'desc': _('Mutual fund NAVs update automatically each day from AMFI. Stocks, ETFs and gold are priced by you, and the app uses the latest price you gave it.'),
                },
                {
                    'title': _('Transfers that know where the money went'),
                    'desc': _('When you move money into an investment account, choose which funds it bought. Every purchase stays linked to that transfer, so "how much went to which fund" always has an answer.'),
                },
                {
                    'title': _('NAV by the market\'s rules'),
                    'desc': _('Purchases follow the cut-off times (3 PM, and 1:30 PM for liquid and overnight funds), roll over weekends and holidays, and deduct the 0.005% stamp duty. A purchase stays marked as estimated until the NAV is published, and you can correct the units to match your contract note.'),
                },
                {
                    'title': _('One SIP, several funds'),
                    'desc': _('Set up a monthly transfer and split it across funds, for example ₹45,000 as three ₹15,000 SIPs. Each instalment buys every fund on its own.'),
                },
                {
                    'title': _('Selling that keeps the books right'),
                    'desc': _('Sell units and the proceeds return as cash in the account, with the profit or loss recorded. Your net worth stays consistent and the account shows any cash waiting to be reinvested.'),
                },
            ],
        },
        {
            'id': 'planning',
            'title': _('Plan ahead'),
            'description': _('Bills, budgets, goals and loans that look after themselves.'),
            'items': [
                {
                    'title': _('Set it once, forget it'),
                    'desc': _('Rent, salary, subscriptions, SIPs and insurance premiums post by themselves on their due dates. You get a reminder three days before, and a Renewing Soon list shows what is coming.'),
                },
                {
                    'title': _('Budgets that warn you early'),
                    'desc': _('Set a monthly limit for each category and see progress bars. You are alerted when a category reaches its warning level and again when it goes over, and a Needs Attention list shows what to look at.'),
                },
                {
                    'title': _('Goals with a finish line'),
                    'desc': _('Set a target, log what you put towards it, and get a projected date based on your real pace. You are alerted at 50%, 90% and 100%.'),
                },
                {
                    'title': _('Loans and a free EMI calculator'),
                    'desc': _('Preview an EMI before you borrow. Then track home, car, personal and other loans with an amortisation schedule, floating-rate changes, repayments and the remaining principal.'),
                },
                {
                    'title': _('Big one-off costs, kept separate'),
                    'desc': _('Record a wedding, a car down payment or a medical bill as a capital event, and choose whether it counts towards your averages, so one big month does not distort your picture.'),
                },
            ],
        },
        {
            'id': 'flows',
            'title': _('Guided setup with TMR Flows'),
            'description': _('Say what happened. The app sets up everything that goes with it.'),
            'items': [
                {
                    'title': _('One short form, everything linked'),
                    'desc': _('Tell it "I took a loan", "I started a new job", "I started a SIP" or "I bought gold". One form creates the account, the schedule and the links, and a preview lists everything before you confirm.'),
                },
                {
                    'title': _('A flow for the moments that matter'),
                    'desc': _('Loans and credit cards, salary, rent and insurance, SIPs, FDs, PPF, EPF and NPS, savings goals, a car and gold. Your progress panel shows which of the core setups you have done.'),
                },
                {
                    'title': _('Safe to get wrong'),
                    'desc': _('You review before anything is saved, a flow saves everything or nothing, and a double-click cannot create things twice. You can switch on historical entries to fill in the months already gone.'),
                },
            ],
        },
        {
            'id': 'family',
            'title': _('Family sharing'),
            'description': _('One household picture, with each person choosing what to share.'),
            'items': [
                {
                    'title': _('One family net worth'),
                    'desc': _("The family sees one combined net worth: what you own, what you owe, and how it splits across cash, investments, property and more. The numbers come from the same valuation as each person's own net worth, so they never disagree."),
                },
                {
                    'title': _('Who holds what'),
                    'desc': _('See every credit card, insurance policy, loan and deposit the family has, and whose it is. A card shows its limit and next bill date, a policy its cover, premium and due date, a loan its EMI.'),
                },
                {
                    'title': _('Nothing is shared until you share it'),
                    'desc': _('Joining a family shares nothing. You pick each account and choose Show amounts or List only. Transactions, categories, notes and the holdings inside an account are never shared.'),
                },
                {
                    'title': _('See exactly what they see'),
                    'desc': _("A preview shows your family's view of your shared accounts. Give an account a nickname if its real name is private. Stop sharing at any time and it disappears for them at once."),
                },
                {
                    'title': _('Emergency access'),
                    'desc': _('Write a short sheet of what exists and where to look, never account numbers or passwords. Choose who may ask. They request it, you can approve or deny, and if you do not answer within 7 days they get a read-only copy.'),
                },
                {
                    'title': _('Gentle notes, never comparisons'),
                    'desc': _('A few calm, optional observations, such as a premium falling due or a card bill coming up, built only from what is shared. They never compare people or judge an amount, and each person can switch them off.'),
                },
                {
                    'title': _('Your own account, your own data'),
                    'desc': _('Each person keeps their own TrackMyRupee account. Family view needs everyone on Pro, with up to 5 people. If a plan lapses, family view pauses and nothing is deleted. Leaving ends all sharing straight away.'),
                },
            ],
        },
        {
            'id': 'noticing',
            'title': _('Insights that notice for you'),
            'description': _('Calm, specific observations from your own entries, so you do not have to go looking.'),
            'items': [
                {
                    'title': _('About fifty things it watches for'),
                    'desc': _('A budget about to run out early, a category creeping up, a deposit about to mature, a card billing date, a salary that is late, idle cash, a subscription that changed price, a goal that is slipping or on track, and more. Each one is a plain sentence about your own money.'),
                },
                {
                    'title': _('Every number opens the entries behind it'),
                    'desc': _('The sentence uses rounded figures. The exact amounts sit in small chips under it, and each chip opens the transactions that make up the number.'),
                },
                {
                    'title': _('Calm by design'),
                    'desc': _('We lead with what is going well, never compare you with other people, and never predict the markets. Insights about debt, a shortfall or a long gap in income are written extra gently, and are never pushed or emailed.'),
                },
                {
                    'title': _('It follows your real month'),
                    'desc': _('Insights use your salary cycle, your Capital Events choices and your currency, the same way your budgets and totals do, so they never disagree with the rest of the app.'),
                },
                {
                    'title': _('You choose how loud it is'),
                    'desc': _('Pick the topics, and whether the bell mentions things normally, quietly or only on the pages. Snooze or dismiss any insight, and say whether it was helpful so you see less of what is not.'),
                },
                {
                    'title': _('Where it shows up'),
                    'desc': _('A short strip on your Dashboard and on pages like Budgets, Expenses, Accounts, Loans and Goals, the sentences on your Analytics tabs, a "What we noticed" card in your Year in Review, and on Pro a weekly email and a monthly report section.'),
                },
                {
                    'title': _('Runs on your entries, nowhere else'),
                    'desc': _('Insights are worked out inside the app from what you logged. No bank feed, no outside AI service, and your data is never sold or used for advertising. Insights are included on every plan.'),
                },
            ],
        },
        {
            'id': 'insights',
            'title': _('Know where you stand'),
            'description': _('Plain answers from your own numbers.'),
            'items': [
                {
                    'title': _('A dashboard you can read at a glance'),
                    'desc': _('Net worth, cash flow, where the money went and a year-to-date projection, for any period you choose.'),
                },
                {
                    'title': _('A health check in four plain numbers'),
                    'desc': _('Your savings rate, whether spending is rising or falling, how many months your cash would last, and how many months you spent less than you earned. Each one says what it measures.'),
                },
                {
                    'title': _('Trends and month-on-month'),
                    'desc': _('See how each category moves over the year and against last month, so a growing habit shows up early.'),
                },
                {
                    'title': _('Year in Review and monthly reports'),
                    'desc': _('A yearly wrap-up of what you earned, spent, saved and invested, and a monthly summary in your inbox.'),
                },
            ],
        },
        {
            'id': 'alerts',
            'title': _('Reminders that reach you'),
            'description': _('In the app, as a push notification, or in one daily email.'),
            'items': [
                {
                    'title': _('Alerts for what is due'),
                    'desc': _('Upcoming bills and premiums, credit card billing dates, budgets nearing or passing their limit, and goal milestones. Each alert is sent once, so you are never nagged.'),
                },
                {
                    'title': _('A 9 pm nudge to log your day'),
                    'desc': _('An optional daily reminder that skips any day you have already added an expense. It works in the browser without installing anything.'),
                },
            ],
        },
        {
            'id': 'trust',
            'title': _('Your data stays yours'),
            'description': _('Privacy by design, not as an afterthought.'),
            'items': [
                {
                    'title': _('No SMS access, no bank login'),
                    'desc': _('Every number comes from your own entries, so there is nothing to connect and no permission to grant.'),
                },
                {
                    'title': _('A double-entry ledger underneath'),
                    'desc': _('Every transaction is recorded on both sides, so balances always add up and a mismatch is flagged instead of hidden.'),
                },
                {
                    'title': _('Export and delete, any time'),
                    'desc': _('Download your full history to CSV on any plan, and delete your account whenever you want. When a paid plan ends, nothing is deleted.'),
                },
                {
                    'title': _('Open source and self-hostable'),
                    'desc': _('The code is public under the AGPL, and you can run the whole app on your own server with Docker.'),
                },
            ],
        },
        {
            'id': 'everywhere',
            'title': _('Works the way you do'),
            'description': _('Your language, your device, your currency.'),
            'items': [
                {
                    'title': _('English, Hindi and Marathi'),
                    'desc': _('The whole app is translated, including Devanagari numerals, so it reads naturally.'),
                },
                {
                    'title': _('An app with no install'),
                    'desc': _('Add it to your home screen and it opens like a native app, with offline reading.'),
                },
                {
                    'title': _('Any currency next to the rupee'),
                    'desc': _('Track USD, EUR or any other currency if you travel, freelance or hold foreign investments. Rates refresh daily and are saved with each transaction.'),
                },
                {
                    'title': _('Light and dark themes'),
                    'desc': _('Follows your device or your choice, on every screen.'),
                },
                {
                    'title': _('Try it before you sign up'),
                    'desc': _('Open the demo with realistic data, no account needed. The Free plan never expires, and a 7-day Pro trial needs no card.'),
                },
            ],
        },
    ]
