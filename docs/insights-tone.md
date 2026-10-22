# Insight tone: how TrackMyRupee talks about money

TrackMyRupee should feel like a calm, kind friend who is good with money, sitting beside you and helping you see your
situation clearly. Never a scold, a salesman or an alarm. These rules are enforced by `expenses/insights/tone.py`, by a
unit test over every rule's output in English, Hindi and Marathi, and by a test that reads every string in the code.

## The shape of a message

1. **What we noticed**, in plain words.
2. **A little context** so it feels normal and fair.
3. **At most one gentle, optional next step.**

> Dining has been a little higher for three months in a row, around ₹9,200 a month. If it feels right to you, you could
> set a limit for it, or leave it as it is.

Lead with what is going well when there is something true to say. Do not manufacture praise.

## Rules

* **Never** use alarm or shame words: warning, danger, overspent, you failed, wasted, bad, careless, you must, you need
  to, urgent, immediately, dangerously. Use "a little higher", "worth a look", "when you have a moment", "one option",
  "you might".
* No exclamation marks, no ALL CAPS (short acronyms such as EMI and SIP are fine), no red-alert framing, no emoji in the
  copy. The card may show a small icon from the icon set the app already has.
* **Normalise before nudging.** Money moves in waves; one high month is common; festivals and travel are normal.
* **Allow for their judgement.** "If that sounds right", "if it is on purpose, you can dismiss this".
* **Specific, not exact to the paisa.** Round in the sentence (about ₹9.2k, about 40%); the exact figure goes in the
  evidence chip, which links to the entries behind it.
* **Honest about uncertainty.** "About", "so far", "at this pace". When there is not enough history, say so ("We are
  still learning your patterns") instead of showing a weak insight.
* **Only their own past.** Never compare a person with other people. No peer benchmarks.
* **Describe, then offer a choice.** Never imply someone should spend more or less on something in a moral sense.
* **Sensitive moments** (debt, a shortfall, a month in the red): extra gentle, no jokes, no share button, never a push.
  Offer one practical, small step.
* **Not financial advice.** No products, no predictions about markets, nothing about what to buy or sell.
* **Short.** Title up to 8 words, message up to 2 sentences, an optional second line. Plain, simple Indian English.
* Every string is gettext with named placeholders, one whole sentence per msgid, with Hindi and Marathi.

## Say this, not that

| Not this | Say this |
|---|---|
| **Traffic Alert!** You're pacing 40% higher than usual. Slow down to stay on track! | **Spending is running a little higher.** At this pace you could end up about 40% above your usual ₹42k. Money moves in waves, so this may simply be a busy stretch. |
| **Budget Breached.** Dining exceeded by ₹3,000, let's rebalance to stay safe. | **Dining went past its limit.** It is about ₹3k over its ₹10k limit. It happens in many months, and you could raise the limit or leave it as it is. |
| **Warning:** you will overspend on Groceries soon. Take care! | **Groceries may reach its limit early.** At this pace it could reach its ₹8k limit around 24 Oct. If that sounds right, you could ease the pace or leave it. |
| You need to cut your Dining spend by 20% to save ₹1,800. | **Dining is a little above usual.** It is about 18% above your recent average so far. One higher stretch is common, especially around festivals and travel. |
| Your FD matures in 12 days. Act now so the money is not wasted! | **A deposit matures soon.** Bank FD matures on 3 Nov, with about ₹2.1L coming back to you. You might decide where it goes next, or leave it for now. |
