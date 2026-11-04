"""Preloaded idea library. IDEAS ONLY — each entry is a brief (angle + concept),
not a produced piece. Full production packages are authored one at a time, on
request, and live in studio/packages/. Everything here is editable in the UI."""

PILLARS = {
    "A": "Relatable money problems", "B": "Financial education", "C": "Product demonstrations",
    "D": "Privacy & trust", "E": "Founder & brand story",
}
STATUSES = ["Idea", "Script ready", "Assets ready", "In production", "Review", "Approved", "Published"]
FORMATS = ["Motion graphic", "Talking head / founder", "Screen recording", "POV skit", "Voiceover + b-roll", "Text-on-screen explainer", "Character skit"]

# (no, title, pillar, format, duration, feature, concept)
_R = [
 (1, "Where does your ₹50,000 salary disappear?", "A", "Motion graphic", "35s", "Quick-add + Top Categories", "One illustrative salary as 50 tiles; follow it until ₹8,000 has no name."),
 (2, "POV: Salary credited!", "A", "POV skit", "20s", "Salary-cycle budgeting", "The 3-second joy of the credit alert vs. the rent/EMI/SIP debits that follow within the hour."),
 (3, "The ₹99 subscription trap", "A", "Text-on-screen explainer", "25s", "Recurring & subscription tracking", "Five forgotten ₹99-type renewals add up to a yearly figure; maths shown on screen."),
 (4, "You invest every month, but do you know your actual net worth?", "B", "Voiceover + b-roll", "30s", "Net worth tracking", "SIP is a habit, net worth is the scoreboard; what a complete picture includes."),
 (5, "The difference between looking rich and being wealthy", "A", "Text-on-screen explainer", "30s", "Net worth tracking", "Visible spending vs. assets minus liabilities; no judgement, just definitions."),
 (6, "What is your actual net worth?", "B", "Motion graphic", "35s", "Net worth tracking", "Assets − liabilities with a worked illustrative example."),
 (7, "Your financial month shouldn't always start on the 1st", "C", "Screen recording", "25s", "Salary-cycle budgeting", "Why budgets should follow salary day (verified feature)."),
 (8, "Assets versus liabilities", "B", "Motion graphic", "30s", "Assets & liabilities", "Two-column sort game: car loan, FD, credit card, gold, EPF."),
 (9, "Why your SIP amount doesn't tell your complete financial story", "B", "Voiceover + b-roll", "30s", "Investment tracking", "SIP amount vs. current value vs. liabilities running alongside."),
 (10, "Five expenses people frequently forget", "A", "Text-on-screen explainer", "30s", "Recurring expenses", "Annual insurance premium, domain/app renewals, festival spends, etc."),
 (11, "Watch me track an expense in three seconds", "C", "Screen recording", "15s", "Natural-language quick add", "Screen recording typing 'swiggy 320' and saving."),
 (12, "One dashboard. Your entire financial life.", "C", "Screen recording", "25s", "Dashboard + net worth", "Real dashboard tour, one number at a time."),
 (13, "Your expenses are telling you something", "A", "Voiceover + b-roll", "30s", "Expense analytics", "Reading patterns: weekends, month-end, stress spends."),
 (14, "What happens when you take a loan?", "B", "Motion graphic", "35s", "Loan & EMI management", "Loan adds an asset and a liability; net worth view with an illustrative home-loan example."),
 (15, "Your money, your privacy", "D", "Text-on-screen explainer", "25s", "Privacy-first design", "No SMS access, no bank login — what that means in plain words."),
 (16, "If your bank account could talk", "D", "Character skit", "25s", "Privacy-first design", "Comedic monologue: everything an app with bank access can see."),
 (17, "Finance apps as people", "D", "Character skit", "30s", "Privacy-first design", "Skit: 'the app that wants every permission' vs 'the app that only knows what you tell it'. Generic archetypes, no named competitors."),
 (18, "The ₹40,000 mystery — founder story", "E", "Talking head / founder", "40s", "Origin story", "The unexplained ₹40,000 that inspired the product. Founder to supply facts."),
 (19, "Things I wish I knew at 22 about money", "E", "Talking head / founder", "35s", "—", "Five short lessons; founder voice."),
 (20, "A day in the life of someone who tracks every rupee", "E", "Voiceover + b-roll", "35s", "Quick add", "Day-in-the-life with 6 tiny log moments."),
 (21, "Why your bank balance isn't your net worth", "B", "Motion graphic", "30s", "Net worth tracking", "Balance ignores FD, MF, loans, card dues."),
 (22, "The salary diary series", "A", "Motion graphic", "35s", "Salary-cycle budgeting", "Series launch: ₹30,000 salary illustrative breakdown."),
 (23, "Money mistakes Monday", "A", "Text-on-screen explainer", "20s", "—", "Series launch: one practical mistake."),
 (24, "Financial reality check", "A", "Text-on-screen explainer", "15s", "—", "Series launch: one uncomfortable, factual truth."),
 (25, "Your credit card limit is not your income", "B", "Motion graphic", "25s", "Credit card tracking", "Limit vs. available vs. owed."),
 (26, "Five things you should track besides expenses", "C", "Text-on-screen explainer", "30s", "Accounts, loans, investments, goals, subscriptions", "Listicle tied to verified features."),
 (27, "What happens when your FD matures?", "B", "Voiceover + b-roll", "30s", "Deposit tracking", "Maturity steps; flag for fact-check (rates/tax rules change)."),
 (28, "Why manual expense tracking can be valuable", "D", "Voiceover + b-roll", "30s", "Manual entry philosophy", "Trade-off: seconds of typing vs. giving away bank/SMS access."),
 (29, "TrackMyRupee versus spreadsheet tracking", "C", "Screen recording", "35s", "Dashboard vs spreadsheet", "Fair comparison with spreadsheets (not competitors)."),
 (30, "Take control of your financial life", "C", "Motion graphic", "25s", "Free demo", "Brand-closer: one-minute brand film cut to 25s."),
]

REELS = [{"id": f"reel-{n:03d}", "kind": "reel", "no": n, "title": t, "pillar": p, "format": f, "duration": d,
          "feature": ft, "concept": c, "status": "Idea", "series": None} for n, t, p, f, d, ft, c in _R]

# (no, title, pillar, slides, linked reel)
_C = [
 (1, "Where does your ₹50,000 salary go?", "A", 8, 1), (2, "Calculate your actual net worth", "B", 9, 6),
 (3, "10 expenses you might be forgetting", "A", 10, 10), (4, "Assets versus liabilities", "B", 8, 8),
 (5, "Five money mistakes young professionals make", "A", 7, None), (6, "The salary-cycle budgeting guide", "C", 8, 7),
 (7, "How to calculate your savings rate", "B", 7, None), (8, "Why your bank balance is misleading", "B", 7, 21),
 (9, "SIP versus actual wealth", "B", 8, 9), (10, "Five things to track besides expenses", "C", 7, 26),
 (11, "How subscriptions drain your money", "A", 7, 3), (12, "How loans affect net worth", "B", 8, 14),
 (13, "Understanding credit card debt", "B", 8, 25), (14, "The importance of an emergency fund", "B", 7, None),
 (15, "Financial checklist for your 20s", "B", 10, 19), (16, "Financial checklist for your 30s", "B", 10, None),
 (17, "How to track your family's wealth", "C", 8, None), (18, "Why financial privacy matters", "D", 8, 15),
 (19, "Manual versus automatic expense tracking", "D", 8, 28), (20, "How to understand your complete financial picture", "B", 9, 12),
 (21, "How to track investments", "C", 8, None), (22, "What is an asset?", "B", 6, 8),
 (23, "What is a liability?", "B", 6, 8), (24, "The hidden cost of lifestyle inflation", "A", 8, 5),
 (25, "Five financial habits worth developing", "B", 7, 20), (26, "Your first net worth calculation", "B", 9, 6),
 (27, "Financial mistakes to avoid after your first salary", "A", 8, 2), (28, "What to review at the end of every month", "B", 8, None),
 (29, "How to organise your financial life", "C", 8, 30), (30, "The ultimate personal finance checklist", "B", 10, 24),
]
CAROUSELS = [{"id": f"carousel-{n:03d}", "kind": "carousel", "no": n, "title": t, "pillar": p, "format": "Carousel 1080×1350",
              "slides": s, "reel_link": r, "feature": "", "concept": "", "status": "Idea", "series": None} for n, t, p, s, r in _C]


def _series(key, name, tag, intro, outro, thumb, colour, numbering, eps):
    return {"id": key, "name": name, "tagline": tag, "intro": intro, "outro": outro, "thumbnail": thumb,
            "colour": colour, "numbering": numbering, "episodes": eps}


SERIES = [
 _series("salary-diaries", "The Salary Diaries", "Same month, different salary.",
         "0.8s: ledger-line wipe, title 'The Salary Diaries · ₹X' in Instrument Serif, tile-grid motif.",
         "Illustrative-example stamp + 'Tell us your salary-day habit' + end card.",
         "Dark teal, big serif salary figure, 50-tile strip in category colours, 'EP 03' chip top-right.",
         "Night teal #06302A→#0B1311; tiles blue/amber/teal; unaccounted tiles coral.", "SD-01, SD-02 … (salary level in title)",
         ["₹30,000 salary", "₹50,000 salary", "₹75,000 salary", "₹1 lakh salary", "₹1.5 lakh salary", "₹2 lakh salary"]),
 _series("money-mistakes-monday", "Money Mistakes Monday", "One practical mistake. Every Monday.",
         "0.6s: calendar flip to 'MON', coral exclamation chip.", "'Seen this one? Save it for later.'",
         "Cream background, coral 'MISTAKE #n' label, black serif headline.", "Cream #F2F0EB, coral #C23E27 accent, ink text.", "MMM-01 …",
         ["Treating the credit-card limit as income", "Ignoring annual premiums", "Keeping idle cash while carrying card dues", "Counting SIP as net worth", "No emergency buffer before investing", "Forgetting subscriptions"]),
 _series("tmr-20", "TrackMyRupee in 20 Seconds", "A feature. A timer. Done.",
         "Stopwatch ring starts at 20.0s over the real UI.", "Timer stops + 'Try it in the free demo'.",
         "Real screenshot crop on teal, '20s' ring badge.", "Teal #0F7657 + white; real UI always from screenshots/recordings.", "20S-01 …",
         ["Log an expense by typing it", "See your net worth", "Set a salary-cycle", "Track a loan EMI", "Add a recurring expense", "Share an account with family"]),
 _series("reality-check", "Financial Reality Check", "Short truths. No lecture.",
         "Black card, one-line truth types in.", "'Agree? Share it.'",
         "Ink-black, single cream serif line, thin teal rule.", "Ink #1A1A19, cream text, teal rule.", "FRC-01 …",
         ["Your bank balance is not your net worth", "A high limit is not high income", "Small leaks outspend big purchases", "Raise ≠ savings unless you save the raise", "What you don't track you can't question"]),
 _series("building-tmr", "Building TrackMyRupee", "Founder notes from the build.",
         "Founder face-cam + commit-log style ticker.", "'Tell me what to build next.'",
         "Founder photo (real), teal frame, 'Build log #n'.", "Teal frame, cream type, real founder footage only.", "BTMR-01 …",
         ["Why no SMS access", "Building TMR Flows", "How family sharing permissions work", "A bug that taught me something", "User feedback that changed the roadmap"]),
 _series("money-myths", "Money Myths", "Myth. Fact. Done.",
         "Split screen: 'MYTH' (coral) → 'FACT' (teal).", "'Which myth did you believe?'",
         "Split cream/teal with strike-through myth text.", "Coral myth / teal fact on cream.", "MM-01 …",
         ["'I don't need to track, I earn well'", "'Net worth is only for the rich'", "'SIP means I'm financially safe'", "'Budgeting means no fun'", "'Privacy apps can't be useful'"]),
 _series("finance-20s", "Finance for Your 20s", "The basics, minus the jargon.",
         "Numbered '20s' badge pops in.", "'Send to a friend who just got their first salary.'",
         "Amber accent, big age-style numeral.", "Amber #F4B44E on night teal.", "F20-01 …",
         ["Your first salary: 5 steps", "What is an emergency fund?", "Credit score basics (fact-check)", "SIP vs savings account (fact-check)", "How to read your payslip"]),
]

HOOKS = [  # (pillar, hook)
 ("A", "Salary on the 1st. Panic on the 20th."), ("A", "Your salary doesn't vanish. It leaks."), ("A", "Be honest: where did last month go?"),
 ("A", "₹99 here, ₹149 there — see you in December."), ("A", "You're not bad with money. You're just not counting."), ("A", "The salary-credited feeling lasts about 40 minutes."),
 ("A", "Why do I feel broke when I earn well?"), ("A", "Three UPI taps you've already forgotten."), ("A", "Rent, EMI, SIP — and then where?"),
 ("A", "Nobody planned that ₹8,000 order history."), ("A", "Check your last 10 UPI payments. I'll wait."), ("A", "Lifestyle inflation sends no notification."),
 ("B", "Your bank balance is lying to you."), ("B", "Net worth in one line: what you own minus what you owe."), ("B", "A ₹10,000 SIP is not a ₹10,000 gain."),
 ("B", "Is a car an asset? Depends who's asking."), ("B", "Savings rate: the number nobody tells you to calculate."), ("B", "Your credit limit is a bank's number, not yours."),
 ("B", "What does an EMI really cost you?"), ("B", "Emergency fund: how many months is 'enough'?"), ("B", "Assets, liabilities — sorted in 20 seconds."),
 ("B", "You can feel rich and be broke. Here's the maths."), ("B", "Loans don't reduce net worth the way you think."), ("B", "Why your FD maturity date matters more than you think."),
 ("C", "Watch an expense get logged in three seconds."), ("C", "Type 'swiggy 320'. That's it."), ("C", "One screen. Everything you own and owe."),
 ("C", "Your salary month starts on salary day — your budget should too."), ("C", "Where is my money going? Tap here."), ("C", "Add a loan and watch net worth update."),
 ("C", "Show me the dashboard, not the pitch."), ("C", "Subscriptions, in one list."), ("C", "Try it before you trust it — free demo, no signup."),
 ("D", "Does your expense app need to read your SMS?"), ("D", "What a finance app can see if you give it your bank login."), ("D", "Your money. Your privacy. Your call."),
 ("D", "No bank login. No SMS access. Still useful?"), ("D", "Convenience has a price. Do you know it?"), ("D", "Who else reads your bank messages?"),
 ("D", "Manual entry: slower, safer, surprisingly useful."), ("D", "If your bank account could talk…"),
 ("E", "I couldn't explain where ₹40,000 went. So I built this."), ("E", "Building a fintech product in India: what nobody warns you about."), ("E", "A user told me my app was wrong. They were right."),
 ("E", "Why we said no to bank sync."), ("E", "Things I wish I knew at 22."), ("E", "Behind the scenes: the feature that took three rewrites."),
 ("A", "What did you spend on last Tuesday? Exactly."), ("B", "Net worth goes up when debt goes down — not only when SIPs go up."), ("D", "Read the permissions before you read the features."),
 ("E", "Shipping a finance app solo — week by week."), ("E", "The feedback that changed our roadmap."),
]

CTAS = [
 "Try the free demo — link in bio.", "See it live in the free demo (no signup).", "Save this for salary day.", "Send this to a friend who's broke by the 20th.",
 "Comment your biggest 'where did it go?' category.", "Follow for one money lesson a day.", "Share this with someone who just got their first salary.",
 "Tap the link in bio and track your first expense.", "Start free at trackmyrupee.com.", "Which one are you? Tell us in the comments.",
 "Screenshot this checklist.", "Try typing your last expense like a message.", "Explore the demo with sample data — no registration.",
 "Check your own net worth — free demo, link in bio.", "Bookmark for month-end review.", "Tag your SIP-buddy.",
 "Want the template? Comment 'CHECKLIST'.", "Tell us what to cover next.", "Open the demo and look at your month differently.",
 "Your data stays yours — see how in the demo.", "DM 'DEMO' and we'll send the link.", "Save now, thank yourself on the 30th.",
]

CAPTION_TEMPLATES = [
 {"name": "Relatable → value → CTA", "body": "{hook}\n\n{story_in_2_lines}\n\nTry this: {one_practical_step}\n\n{cta}\n\nNumbers shown are illustrative. Not financial advice."},
 {"name": "Education list", "body": "{hook}\n\n{n} things to know:\n1. {a}\n2. {b}\n3. {c}\n\nSave this for later. {cta}\n\nIllustrative examples only."},
 {"name": "Product demo", "body": "{hook}\n\n{what_the_feature_does_in_1_line}\n\nNo bank login. No SMS access.\n\n{cta}"},
 {"name": "Privacy", "body": "{hook}\n\n{plain_language_explanation}\n\nTrackMyRupee only has what you enter. {cta}"},
 {"name": "Founder story", "body": "{hook}\n\n{story}\n\nWhat would you like us to build next? Tell me below."},
]

HASHTAG_TEMPLATES = {
 "A": ["#PersonalFinanceIndia", "#SalaryManagement", "#ExpenseTracker", "#MoneyTipsIndia", "#TrackMyRupee"],
 "B": ["#FinancialLiteracy", "#NetWorth", "#PersonalFinanceIndia", "#MoneyManagement", "#TrackMyRupee"],
 "C": ["#ExpenseTracker", "#BudgetingApp", "#PersonalFinanceIndia", "#MoneyManagement", "#TrackMyRupee"],
 "D": ["#FinancialPrivacy", "#DataPrivacy", "#PersonalFinanceIndia", "#ExpenseTracker", "#TrackMyRupee"],
 "E": ["#BuildInPublic", "#IndianStartups", "#FintechIndia", "#FounderStory", "#TrackMyRupee"],
}

BRAND = {
 "name": "TrackMyRupee", "website": "https://trackmyrupee.com", "demo": "https://trackmyrupee.com/demo", "logo": "static/img/logo.svg",
 "colors": {"primary": "#0F7657", "primary_light": "#5DCAA5", "primary_dark": "#04342C", "expense_coral": "#C23E27", "coral_light": "#F49C8C",
            "alert_amber": "#F4B44E", "investment_blue": "#85B7EB", "ink": "#1A1A19", "cream": "#F2F0EB", "night_top": "#06302A", "night_bot": "#0B1311"},
 "fonts": {"display": "Instrument Serif", "ui": "DM Sans", "hindi": "Noto Sans Devanagari"},
 "sizes": {"reel": "1080×1920", "carousel": "1080×1350", "cover": "1080×1920 (key content inside the central 1080×1350)"},
 "safe_zone": "Reels: 150px top, 280px bottom, 90px sides.",
 "verified_claims": [
  "No SMS access, no bank login, no email scanning", "Type expenses like a message (e.g. 'swiggy 320')", "Budgets follow your salary cycle",
  "Net worth across bank accounts, mutual funds, stocks, FDs, PPF, EPF, NPS and loans", "Family sharing: you choose what to share; transactions are never shared",
  "English, Hindi, Marathi; installable web app (PWA)", "Free plan that does not expire; Plus ₹49/month or ₹499/year (verify before quoting)",
  "Rule-based insights computed inside the app — no outside AI service", "Free demo at trackmyrupee.com/demo", "Open source (AGPL), self-hostable"],
 "never_claim": ["automatic bank sync", "automatic transaction import", "automatic investment sync (note: MF NAVs update daily from AMFI, stocks/gold are user-priced)",
                 "AI-powered insights", "guaranteed savings or returns", "competitor comparisons"],
 "approved_style": None,
}

# 30-day calendar: reel no. per day; carousels on days 2,4,6 of each week
_REEL_ORDER = [1, 11, 4, 15, 3, 18, 6, 2, 12, 16, 8, 22, 19, 9, 17, 7, 14, 28, 10, 26, 21, 23, 20, 25, 5, 29, 13, 27, 24, 30]
_CAR_DAYS = [2, 4, 6, 9, 11, 13, 16, 18, 20, 23, 25, 27, 30]


def build_calendar(start="2026-11-09"):
    import datetime as dt
    assert sorted(_REEL_ORDER) == list(range(1, 31))
    reels = {r["no"]: r for r in REELS}
    used, out = set(), []
    d0 = dt.date.fromisoformat(start)
    for day, rn in enumerate(_REEL_ORDER, 1):
        r = reels[rn]
        date = (d0 + dt.timedelta(days=day - 1)).isoformat()
        out.append({"day": day, "date": date, "content_id": r["id"], "title": r["title"], "pillar": r["pillar"], "format": "Reel",
                    "audience": "Salaried professionals 22–35", "objective": _obj(r["pillar"]), "cta": CTAS[(day * 3) % len(CTAS)],
                    "assets": "Cover, video, VO, music, captions", "status": "Idea", "story": "Story: poll or question sticker + Reel share"})
        if day in _CAR_DAYS:
            c = next((c for c in CAROUSELS if c["reel_link"] == rn and c["no"] not in used), None) \
                or next(c for c in CAROUSELS if c["pillar"] != r["pillar"] and c["no"] not in used)
            used.add(c["no"])
            out.append({"day": day, "date": date, "content_id": c["id"], "title": c["title"], "pillar": c["pillar"], "format": "Carousel",
                        "audience": "Salaried professionals 22–35", "objective": "Saves and shares", "cta": "Save this for later.",
                        "assets": "10 slides PNG + PDF", "status": "Idea", "story": "Story: slide 1 + 'swipe' sticker"})
    return out


def _obj(p):
    return {"A": "Shares & relatability", "B": "Saves & authority", "C": "Demo clicks", "D": "Trust & follows", "E": "Follows & connection"}[p]


def seed():
    return {"ideas": REELS + CAROUSELS, "series": SERIES, "calendar": build_calendar(), "hooks": [{"pillar": p, "text": t} for p, t in HOOKS],
            "ctas": CTAS, "caption_templates": CAPTION_TEMPLATES, "hashtag_templates": HASHTAG_TEMPLATES, "brand": BRAND}
