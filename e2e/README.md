# Browser (end to end) tests

These tests drive a real Chromium through the app, the way a person does: sign in, add an expense, move money, pay an
EMI, switch the language, open every page on a computer and on a phone. They fail when a page throws a script error,
when a button does nothing, or when a flow stops saving, which unit tests cannot see.

They use Django's live test server and a throwaway database, so they never touch real data.

## Run them

```bash
pip install -r requirements-e2e.txt
playwright install chromium          # once: downloads the browser

RUN_E2E=1 python manage.py test e2e --noinput
```

Without `RUN_E2E=1` they are skipped, so the normal `python manage.py test` stays fast and needs no browser.

If a Chromium is already installed (for example in a container), point at it instead of downloading one:

```bash
RUN_E2E=1 E2E_CHROMIUM_PATH=/path/to/chrome python manage.py test e2e --noinput
```

Run one file or one test: `RUN_E2E=1 python manage.py test e2e.test_move_money --noinput`.

## What they cover

| File | Flows |
|---|---|
| `test_smoke.py` | Every main page opens for a person with real data (computer and phone), shows its heading, no server error, no script error |
| `test_add_expense.py` | Type an expense, add it, find it in the list and on the account; on a phone; "Add another" and Undo |
| `test_move_money.py` | Balances change; the notes for money going into, out of and between investments; a renewed FD counts only the new money |
| `test_accounts.py` | Sign in and out through the real form; add an account by kind and type, and by typing; the deposit hint |
| `test_deposit_maturity.py` | The "When it matures" card: Add interest opens income for that account, Move Money opens from it |
| `test_everyday.py` | Edit and delete an expense, Pay EMI, goal contribution, language switch, Analytics tabs, the phone bottom bar and More sheet, the first-run redirect |

## Writing a new one

Subclass `E2ETestCase` (`e2e/base.py`). The helpers:

* `make_user(...)`, `make_account(...)`: a signed-in-able Pro user and accounts.
* `self.new_page(user, phone=False)`: a page already signed in as `user` (no need to go through the sign-in form).
* `self.open(page, path)`: go to a path; it must answer with a success.
* `self.set_select(page, selector, value)`: pick an option of a restyled dropdown.
* `self.wait_until(check)`: wait for something the app saves a moment after a click.

Two things are checked for you on every test: an uncaught script error fails the test (even if the page looked right), and
a failing test saves a screenshot of every page to `e2e/artifacts/` (not committed).

Prefer waiting for what you need (`page.wait_for_selector`, `page.wait_for_url`, `self.wait_until`) over fixed sleeps or
"network idle": a page may hold requests open.

Scripts loaded from a CDN (the tour, the confetti) are unreachable in some sandboxes; the errors that causes are listed in
`IGNORED_ERRORS` in `e2e/base.py`.
