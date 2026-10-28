from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from expenses.ledger_rollout import is_user_in_read_cohort


class LedgerRolloutTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="cohort_user", password="pass")

    @override_settings(LEDGER_READ_ENABLED=False, LEDGER_READ_COHORT_PERCENT=100)
    def test_cohort_disabled_when_read_flag_off(self):
        self.assertFalse(is_user_in_read_cohort(self.user))

    @override_settings(LEDGER_READ_ENABLED=True, LEDGER_READ_COHORT_PERCENT=0)
    def test_zero_percent_means_no_users(self):
        self.assertFalse(is_user_in_read_cohort(self.user))

    def test_include_list_overrides_percent(self):
        # The user's real id: PostgreSQL does not restart its id sequence between tests, so it is not 1.
        with override_settings(LEDGER_READ_ENABLED=True, LEDGER_READ_COHORT_PERCENT=0, LEDGER_READ_COHORT_USER_IDS={self.user.id}):
            self.assertTrue(is_user_in_read_cohort(self.user))

    def test_exclude_list_overrides_include_and_percent(self):
        with override_settings(LEDGER_READ_ENABLED=True, LEDGER_READ_COHORT_PERCENT=100, LEDGER_READ_EXCLUDE_USER_IDS={self.user.id}):
            self.assertFalse(is_user_in_read_cohort(self.user))

    @override_settings(LEDGER_READ_ENABLED=True, LEDGER_READ_COHORT_PERCENT=100)
    def test_full_percent_enables_all(self):
        self.assertTrue(is_user_in_read_cohort(self.user))
