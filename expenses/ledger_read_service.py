import logging
import random
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.db.models.functions import Coalesce

from .ledger_rollout import is_user_in_read_cohort
from .models import JournalEntry, JournalLine, Loan, SavingsGoal
from .utils import get_exchange_rate

logger = logging.getLogger(__name__)


class LedgerReadService:
    @staticmethod
    def is_enabled(user=None):
        if user is None:
            return getattr(settings, "LEDGER_READ_ENABLED", False)
        return is_user_in_read_cohort(user)

    @staticmethod
    def _compare_logging_enabled():
        return getattr(settings, "LEDGER_READ_COMPARE_ENABLED", False)

    @staticmethod
    def _compare_sample_rate():
        try:
            return float(getattr(settings, "LEDGER_READ_COMPARE_SAMPLE_RATE", 1.0))
        except (TypeError, ValueError):
            return 1.0

    @classmethod
    def _log_comparison(cls, *, account, selected_balance, ledger_delta, used_fallback, has_opening_entry):
        if not cls._compare_logging_enabled():
            return
        if random.random() > max(0.0, min(1.0, cls._compare_sample_rate())):
            return

        logger.info(
            "ledger_read_compare",
            extra={
                "user_id": account.user_id,
                "account_id": account.id,
                "account_currency": account.currency,
                "model_balance": str(account.balance),
                "ledger_delta": str(ledger_delta),
                "selected_balance": str(selected_balance),
                "used_fallback": used_fallback,
                "has_opening_entry": has_opening_entry,
            },
        )

    @classmethod
    def _line_amount_in_account_currency(cls, line, account):
        if line.currency == account.currency:
            return line.amount
        rate = get_exchange_rate(line.currency, account.currency)
        return (line.amount * rate).quantize(Decimal("0.01"))

    @classmethod
    def _get_opening_account_ids(cls, user):
        opening_account_ids = set(
            JournalEntry.objects.filter(
                user=user,
                source_type="ADJUSTMENT",
                status="POSTED",
                metadata__has_key="opening_account_id",
            ).values_list("metadata__opening_account_id", flat=True)
        )
        return {int(value) for value in opening_account_ids if value is not None}

    @classmethod
    def get_account_balances(cls, accounts):
        accounts = list(accounts)
        if not accounts:
            return {}

        user = accounts[0].user
        if not cls.is_enabled(user):
            return {account.id: account.balance for account in accounts}

        account_ids = [account.id for account in accounts]
        account_map = {account.id: account for account in accounts}
        lines = JournalLine.objects.filter(
            account_ref_id__in=account_ids,
            journal_entry__status="POSTED",
        ).only("account_ref_id", "direction", "amount", "currency")

        lines_by_account = {account_id: [] for account_id in account_ids}
        for line in lines:
            lines_by_account[line.account_ref_id].append(line)

        opening_account_ids = cls._get_opening_account_ids(user)
        balances = {}

        for account_id in account_ids:
            account = account_map[account_id]
            debit = Decimal("0.00")
            credit = Decimal("0.00")
            for line in lines_by_account[account_id]:
                converted = cls._line_amount_in_account_currency(line, account)
                if line.direction == "DEBIT":
                    debit += converted
                else:
                    credit += converted

            ledger_delta = (debit - credit).quantize(Decimal("0.01"))
            has_opening_entry = account_id in opening_account_ids
            selected_balance = ledger_delta if has_opening_entry else account.balance

            cls._log_comparison(
                account=account,
                selected_balance=selected_balance,
                ledger_delta=ledger_delta,
                used_fallback=not has_opening_entry,
                has_opening_entry=has_opening_entry,
            )
            balances[account_id] = selected_balance

        return balances

    @classmethod
    def get_account_ledger_delta(cls, account):
        lines = JournalLine.objects.filter(
            account_ref=account,
            journal_entry__status="POSTED",
        ).only("direction", "amount", "currency")

        debit = Decimal("0.00")
        credit = Decimal("0.00")
        for line in lines:
            converted = cls._line_amount_in_account_currency(line, account)
            if line.direction == "DEBIT":
                debit += converted
            else:
                credit += converted

        return (debit - credit).quantize(Decimal("0.01"))

    @classmethod
    def get_account_balance(cls, account):
        if not cls.is_enabled(account.user):
            return account.balance

        # Until opening balances are explicitly journaled during backfill,
        # fallback to model balance to avoid regressions for existing accounts.
        return cls.get_account_balances([account]).get(account.id, account.balance)

    @classmethod
    def get_net_worth(cls, user):
        accounts = list(user.accounts.filter(is_active=True))
        if not accounts:
            base_currency = user.profile.currency

            # Even when no active accounts exist, preserve accounting semantics:
            # net worth = goal reserves - outstanding liabilities.
            goal_reserves_base = Decimal("0.00")
            for goal in SavingsGoal.objects.filter(user=user):
                goal_amount = goal.current_amount or Decimal("0.00")
                if goal.currency != base_currency:
                    rate = get_exchange_rate(goal.currency, base_currency)
                    goal_amount = (goal_amount * rate).quantize(Decimal("0.01"))
                goal_reserves_base += goal_amount

            outstanding_loan_base = Decimal("0.00")
            active_loans = Loan.objects.filter(user=user, is_active=True).annotate(
                paid_principal=Coalesce(Sum("repayments__principal_portion"), Decimal("0.00"))
            )
            for loan in active_loans:
                remaining_principal = (loan.initial_principal - loan.paid_principal).quantize(Decimal("0.01"))
                if remaining_principal <= Decimal("0.00"):
                    continue

                if loan.currency != base_currency:
                    rate = get_exchange_rate(loan.currency, base_currency)
                    remaining_principal = (remaining_principal * rate).quantize(Decimal("0.01"))
                outstanding_loan_base += remaining_principal

            physical_assets_base = Decimal("0.00")
            if getattr(settings, "NET_WORTH_EXTENDED_MODELS_ENABLED", False):
                from .models import PhysicalAsset
                assets = PhysicalAsset.objects.filter(user=user, is_active=True)
                for asset in assets:
                    latest_val = asset.valuations.order_by('-as_of_date', '-created_at').first()
                    if latest_val:
                        val = latest_val.value
                        if asset.currency != base_currency:
                            rate = get_exchange_rate(asset.currency, base_currency)
                            val = (val * rate).quantize(Decimal("0.01"))
                        physical_assets_base += val

            return (goal_reserves_base - outstanding_loan_base + physical_assets_base).quantize(Decimal("0.01")), {}

        base_currency = user.profile.currency
        account_ids = [a.id for a in accounts]
        account_map = {a.id: a for a in accounts}

        # 1 query: all posted journal lines for all active accounts
        all_lines = JournalLine.objects.filter(
            account_ref_id__in=account_ids,
            journal_entry__status="POSTED",
        ).only("account_ref_id", "direction", "amount", "currency")

        lines_by_account: dict = {aid: [] for aid in account_ids}
        for line in all_lines:
            lines_by_account[line.account_ref_id].append(line)

        # 1 query: opening entry account IDs for this user
        opening_account_ids = cls._get_opening_account_ids(user)

        net_worth = Decimal("0.00")
        account_base_balances = {}

        for account in accounts:
            lines = lines_by_account[account.id]
            debit = Decimal("0.00")
            credit = Decimal("0.00")
            for line in lines:
                converted = cls._line_amount_in_account_currency(line, account)
                if line.direction == "DEBIT":
                    debit += converted
                else:
                    credit += converted
            ledger_delta = (debit - credit).quantize(Decimal("0.01"))

            has_opening_entry = account.id in opening_account_ids
            if has_opening_entry:
                balance = ledger_delta
            else:
                balance = account.balance

            cls._log_comparison(
                account=account,
                selected_balance=balance,
                ledger_delta=ledger_delta,
                used_fallback=not has_opening_entry,
                has_opening_entry=has_opening_entry,
            )

            if account.currency == base_currency:
                converted_bal = balance
            else:
                rate = get_exchange_rate(account.currency, base_currency)
                converted_bal = (balance * rate).quantize(Decimal("0.01"))
                
            if getattr(settings, "NET_WORTH_EXTENDED_MODELS_ENABLED", False) and account.account_type == 'INVESTMENT':
                from .models import Holding
                holdings = Holding.objects.filter(account=account, is_active=True)
                if holdings.exists():
                    holdings_val = Decimal("0.00")
                    for holding in holdings:
                        latest_val = holding.valuations.order_by('-as_of_date', '-created_at').first()
                        if latest_val:
                            val = latest_val.value
                            if holding.currency != base_currency:
                                rate = get_exchange_rate(holding.currency, base_currency)
                                val = (val * rate).quantize(Decimal("0.01"))
                            holdings_val += val
                    converted_bal = holdings_val

            account_base_balances[account.pk] = converted_bal
            net_worth += converted_bal

        # Goal contributions represent internal earmarking of assets. Include goal
        # reserves so net worth is not reduced by moving money into goals.
        goal_reserves_base = Decimal("0.00")
        for goal in SavingsGoal.objects.filter(user=user):
            goal_amount = goal.current_amount or Decimal("0.00")
            if goal.currency != base_currency:
                rate = get_exchange_rate(goal.currency, base_currency)
                goal_amount = (goal_amount * rate).quantize(Decimal("0.01"))
            goal_reserves_base += goal_amount

        # Subtract outstanding loan principal to reflect liabilities.
        outstanding_loan_base = Decimal("0.00")
        active_loans = Loan.objects.filter(user=user, is_active=True).annotate(
            paid_principal=Coalesce(Sum("repayments__principal_portion"), Decimal("0.00"))
        )
        for loan in active_loans:
            remaining_principal = (loan.initial_principal - loan.paid_principal).quantize(Decimal("0.01"))
            if remaining_principal <= Decimal("0.00"):
                continue

            if loan.currency != base_currency:
                rate = get_exchange_rate(loan.currency, base_currency)
                remaining_principal = (remaining_principal * rate).quantize(Decimal("0.01"))
            outstanding_loan_base += remaining_principal

        physical_assets_base = Decimal("0.00")
        if getattr(settings, "NET_WORTH_EXTENDED_MODELS_ENABLED", False):
            from .models import PhysicalAsset
            assets = PhysicalAsset.objects.filter(user=user, is_active=True)
            for asset in assets:
                latest_val = asset.valuations.order_by('-as_of_date', '-created_at').first()
                if latest_val:
                    val = latest_val.value
                    if asset.currency != base_currency:
                        rate = get_exchange_rate(asset.currency, base_currency)
                        val = (val * rate).quantize(Decimal("0.01"))
                    physical_assets_base += val

        net_worth = (net_worth + goal_reserves_base - outstanding_loan_base + physical_assets_base).quantize(Decimal("0.01"))
        return net_worth, account_base_balances
