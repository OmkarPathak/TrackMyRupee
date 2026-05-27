from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (
    Account,
    Expense,
    Income,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    Loan,
    Transfer,
)
from .utils import get_exchange_rate


class LedgerPostingService:
    """Creates balanced journal entries with idempotent writes."""

    @staticmethod
    def _has_fk(obj, fk_name):
        fk_id = getattr(obj, f"{fk_name}_id", None)
        if fk_id is not None:
            return True
        return getattr(obj, fk_name, None) is not None

    @staticmethod
    def _to_base_amount(user, amount, currency):
        try:
            base_currency = user.profile.currency
        except Exception:
            # Fallback: use Indian Rupee as default if user has no profile
            base_currency = '₹'
        if currency == base_currency:
            return Decimal("1.0"), amount
        fx_rate = get_exchange_rate(currency, base_currency)
        base_amount = (amount * fx_rate).quantize(Decimal("0.01"))
        return fx_rate, base_amount

    @staticmethod
    def _normalize_code(value):
        return "".join(ch if ch.isalnum() else "_" for ch in value.strip().upper())

    @classmethod
    def _get_or_create_account_ledger(cls, user, account):
        code = f"USR:{user.id}:ASSET:ACCOUNT:{account.id}"
        defaults = {
            "user": user,
            "name": f"Asset - {account.name}",
            "account_type": "ASSET",
            "currency": account.currency,
            "is_active": True,
        }
        ledger_account, _ = LedgerAccount.objects.get_or_create(code=code, defaults=defaults)
        return ledger_account

    @classmethod
    def _get_or_create_expense_ledger(cls, user, category, currency):
        normalized_category = cls._normalize_code(category or "UNCATEGORIZED")
        code = f"USR:{user.id}:EXPENSE:CATEGORY:{normalized_category}"
        defaults = {
            "user": user,
            "name": f"Expense - {category or 'Uncategorized'}",
            "account_type": "EXPENSE",
            "currency": currency,
            "is_active": True,
        }
        ledger_account, _ = LedgerAccount.objects.get_or_create(code=code, defaults=defaults)
        return ledger_account

    @classmethod
    def _get_or_create_income_ledger(cls, user, source, currency):
        normalized_source = cls._normalize_code(source or "OTHER")
        code = f"USR:{user.id}:INCOME:SOURCE:{normalized_source}"
        defaults = {
            "user": user,
            "name": f"Income - {source or 'Other'}",
            "account_type": "INCOME",
            "currency": currency,
            "is_active": True,
        }
        ledger_account, _ = LedgerAccount.objects.get_or_create(code=code, defaults=defaults)
        return ledger_account

    @classmethod
    def _get_or_create_loan_liability_ledger(cls, user, loan):
        code = f"USR:{user.id}:LIABILITY:LOAN:{loan.id}"
        defaults = {
            "user": user,
            "name": f"Loan Liability - {loan.name}",
            "account_type": "LIABILITY",
            "currency": loan.currency,
            "is_active": True,
        }
        ledger_account, _ = LedgerAccount.objects.get_or_create(code=code, defaults=defaults)
        return ledger_account

    @classmethod
    def _build_line(cls, *, entry, ledger_account, direction, amount, currency, user, account_ref=None):
        if amount <= 0:
            raise ValidationError("Journal line amount must be positive.")
        fx_rate, base_amount = cls._to_base_amount(user, amount, currency)
        return JournalLine(
            journal_entry=entry,
            ledger_account=ledger_account,
            direction=direction,
            amount=amount,
            currency=currency,
            fx_rate_to_base=fx_rate,
            base_amount=base_amount,
            account_ref=account_ref,
        )

    @staticmethod
    def _validate_balanced(lines):
        debit = sum((line.base_amount for line in lines if line.direction == "DEBIT"), Decimal("0.00"))
        credit = sum((line.base_amount for line in lines if line.direction == "CREDIT"), Decimal("0.00"))
        if debit.quantize(Decimal("0.01")) != credit.quantize(Decimal("0.01")):
            raise ValidationError("Unbalanced journal entry in base currency.")

    @staticmethod
    def _idempotency_key(source_type, source_id, version_token):
        return f"{source_type}:{source_id}:{version_token}"

    @classmethod
    def _create_entry(
        cls,
        *,
        user,
        source_type,
        source_id,
        idempotency_key,
        description,
        metadata,
        lines,
        status="POSTED",
    ):
        cls._validate_balanced(lines)
        with transaction.atomic():
            try:
                entry, created = JournalEntry.objects.get_or_create(
                    idempotency_key=idempotency_key,
                    defaults={
                        "user": user,
                        "source_type": source_type,
                        "source_id": source_id,
                        "description": description,
                        "metadata": metadata or {},
                        "status": status,
                    },
                )
            except IntegrityError:
                entry = JournalEntry.objects.get(idempotency_key=idempotency_key)
                created = False

            if not created:
                return entry, False

            for line in lines:
                line.journal_entry = entry
            JournalLine.objects.bulk_create(lines)
            return entry, True

    @classmethod
    def post_expense(cls, *, expense, idempotency_key, metadata=None):
        user = expense.user
        if expense.account is None:
            raise ValidationError("Expense must be linked to an account for ledger posting.")

        expense_ledger = cls._get_or_create_expense_ledger(user, expense.category, expense.currency)
        asset_ledger = cls._get_or_create_account_ledger(user, expense.account)

        lines = [
            cls._build_line(
                entry=None,
                ledger_account=expense_ledger,
                direction="DEBIT",
                amount=expense.amount,
                currency=expense.currency,
                user=user,
            ),
            cls._build_line(
                entry=None,
                ledger_account=asset_ledger,
                direction="CREDIT",
                amount=expense.amount,
                currency=expense.currency,
                user=user,
                account_ref=expense.account,
            ),
        ]

        return cls._create_entry(
            user=user,
            source_type="EXPENSE",
            source_id=expense.id,
            idempotency_key=idempotency_key,
            description=expense.description,
            metadata=metadata,
            lines=lines,
        )

    @classmethod
    def post_income(cls, *, income, idempotency_key, metadata=None):
        user = income.user
        if income.account is None:
            raise ValidationError("Income must be linked to an account for ledger posting.")

        asset_ledger = cls._get_or_create_account_ledger(user, income.account)
        income_ledger = cls._get_or_create_income_ledger(user, income.source, income.currency)

        lines = [
            cls._build_line(
                entry=None,
                ledger_account=asset_ledger,
                direction="DEBIT",
                amount=income.amount,
                currency=income.currency,
                user=user,
                account_ref=income.account,
            ),
            cls._build_line(
                entry=None,
                ledger_account=income_ledger,
                direction="CREDIT",
                amount=income.amount,
                currency=income.currency,
                user=user,
            ),
        ]

        return cls._create_entry(
            user=user,
            source_type="INCOME",
            source_id=income.id,
            idempotency_key=idempotency_key,
            description=income.description or income.source,
            metadata=metadata,
            lines=lines,
        )

    @classmethod
    def post_transfer(cls, *, transfer, idempotency_key, metadata=None):
        user = transfer.user
        source_ledger = cls._get_or_create_account_ledger(user, transfer.from_account)
        destination_ledger = cls._get_or_create_account_ledger(user, transfer.to_account)

        lines = [
            cls._build_line(
                entry=None,
                ledger_account=destination_ledger,
                direction="DEBIT",
                amount=transfer.amount,
                currency=transfer.from_account.currency,
                user=user,
                account_ref=transfer.to_account,
            ),
            cls._build_line(
                entry=None,
                ledger_account=source_ledger,
                direction="CREDIT",
                amount=transfer.amount,
                currency=transfer.from_account.currency,
                user=user,
                account_ref=transfer.from_account,
            ),
        ]

        return cls._create_entry(
            user=user,
            source_type="TRANSFER",
            source_id=transfer.id,
            idempotency_key=idempotency_key,
            description=transfer.description or "Account transfer",
            metadata=metadata,
            lines=lines,
        )

    @classmethod
    def post_loan_repayment(cls, *, repayment, idempotency_key, metadata=None):
        user = repayment.loan.user
        if repayment.from_account is None:
            raise ValidationError("Loan repayment must include a paying account for ledger posting.")

        paying_asset_ledger = cls._get_or_create_account_ledger(user, repayment.from_account)
        loan_liability_ledger = cls._get_or_create_loan_liability_ledger(user, repayment.loan)
        interest_expense_ledger = cls._get_or_create_expense_ledger(
            user,
            f"Loan Interest - {repayment.loan.name}",
            repayment.loan.currency,
        )

        lines = []
        if repayment.principal_portion > 0:
            lines.append(
                cls._build_line(
                    entry=None,
                    ledger_account=loan_liability_ledger,
                    direction="DEBIT",
                    amount=repayment.principal_portion,
                    currency=repayment.loan.currency,
                    user=user,
                )
            )

        if repayment.interest_portion > 0:
            lines.append(
                cls._build_line(
                    entry=None,
                    ledger_account=interest_expense_ledger,
                    direction="DEBIT",
                    amount=repayment.interest_portion,
                    currency=repayment.loan.currency,
                    user=user,
                )
            )

        lines.append(
            cls._build_line(
                entry=None,
                ledger_account=paying_asset_ledger,
                direction="CREDIT",
                amount=repayment.amount,
                currency=repayment.loan.currency,
                user=user,
                account_ref=repayment.from_account,
            )
        )

        return cls._create_entry(
            user=user,
            source_type="LOAN_REPAYMENT",
            source_id=repayment.id,
            idempotency_key=idempotency_key,
            description=f"Loan repayment for {repayment.loan.name}",
            metadata=metadata,
            lines=lines,
        )

    @classmethod
    def post_opening_balance(cls, *, account, idempotency_key=None, metadata=None):
        """Backfills an opening balance adjustment entry for an account."""
        user = account.user
        amount = abs(account.balance)
        if amount == 0:
            return None, False

        asset_ledger = cls._get_or_create_account_ledger(user, account)
        equity_ledger, _ = LedgerAccount.objects.get_or_create(
            code=f"USR:{user.id}:EQUITY:OPENING_BALANCE",
            defaults={
                "user": user,
                "name": "Opening Balance Equity",
                "account_type": "EQUITY",
                "currency": account.currency,
                "is_active": True,
            },
        )

        if account.balance >= 0:
            debit_ledger, credit_ledger = asset_ledger, equity_ledger
        else:
            debit_ledger, credit_ledger = equity_ledger, asset_ledger

        lines = [
            cls._build_line(
                entry=None,
                ledger_account=debit_ledger,
                direction="DEBIT",
                amount=amount,
                currency=account.currency,
                user=user,
                account_ref=account if debit_ledger == asset_ledger else None,
            ),
            cls._build_line(
                entry=None,
                ledger_account=credit_ledger,
                direction="CREDIT",
                amount=amount,
                currency=account.currency,
                user=user,
                account_ref=account if credit_ledger == asset_ledger else None,
            ),
        ]

        key = idempotency_key or cls._idempotency_key("ADJUSTMENT", account.id, "OPENING")
        payload = {
            "opening_account_id": account.id,
            "opening_balance": str(account.balance),
            "currency": account.currency,
            "kind": "OPENING_BALANCE",
        }
        if metadata:
            payload.update(metadata)

        return cls._create_entry(
            user=user,
            source_type="ADJUSTMENT",
            source_id=account.id,
            idempotency_key=key,
            description=f"Opening balance backfill for {account.name}",
            metadata=payload,
            lines=lines,
        )

    @classmethod
    def post_account_balance_adjustment(cls, *, account, delta, version_token, metadata=None):
        """Posts an ADJUSTMENT entry when an account balance is manually edited."""
        user = account.user
        delta = Decimal(str(delta)).quantize(Decimal("0.01"))
        if delta == Decimal("0.00"):
            return None, False

        amount = abs(delta)
        asset_ledger = cls._get_or_create_account_ledger(user, account)
        equity_ledger, _ = LedgerAccount.objects.get_or_create(
            code=f"USR:{user.id}:EQUITY:MANUAL_BALANCE_ADJUSTMENT",
            defaults={
                "user": user,
                "name": "Manual Balance Adjustment Equity",
                "account_type": "EQUITY",
                "currency": account.currency,
                "is_active": True,
            },
        )

        # Positive delta means asset increased (debit asset, credit equity).
        if delta > 0:
            debit_ledger, credit_ledger = asset_ledger, equity_ledger
        else:
            debit_ledger, credit_ledger = equity_ledger, asset_ledger

        lines = [
            cls._build_line(
                entry=None,
                ledger_account=debit_ledger,
                direction="DEBIT",
                amount=amount,
                currency=account.currency,
                user=user,
                account_ref=account if debit_ledger == asset_ledger else None,
            ),
            cls._build_line(
                entry=None,
                ledger_account=credit_ledger,
                direction="CREDIT",
                amount=amount,
                currency=account.currency,
                user=user,
                account_ref=account if credit_ledger == asset_ledger else None,
            ),
        ]

        payload = {
            "kind": "MANUAL_BALANCE_EDIT",
            "account_id": account.id,
            "currency": account.currency,
            "delta": str(delta),
            "version": version_token,
        }
        if metadata:
            payload.update(metadata)

        return cls._create_entry(
            user=user,
            source_type="ADJUSTMENT",
            source_id=account.id,
            idempotency_key=cls._idempotency_key("ADJUSTMENT", account.id, version_token),
            description=f"Manual balance adjustment for {account.name}",
            metadata=payload,
            lines=lines,
        )

    @classmethod
    def _post_expense_reversal(cls, *, expense, idempotency_key, metadata=None):
        user = expense.user
        if expense.account is None:
            return None, False

        expense_ledger = cls._get_or_create_expense_ledger(user, expense.category, expense.currency)
        asset_ledger = cls._get_or_create_account_ledger(user, expense.account)

        lines = [
            cls._build_line(
                entry=None,
                ledger_account=asset_ledger,
                direction="DEBIT",
                amount=expense.amount,
                currency=expense.currency,
                user=user,
                account_ref=expense.account,
            ),
            cls._build_line(
                entry=None,
                ledger_account=expense_ledger,
                direction="CREDIT",
                amount=expense.amount,
                currency=expense.currency,
                user=user,
            ),
        ]

        return cls._create_entry(
            user=user,
            source_type="EXPENSE",
            source_id=expense.id,
            idempotency_key=idempotency_key,
            description=f"Reversal: {expense.description}",
            metadata=metadata,
            lines=lines,
            status="REVERSED",
        )

    @classmethod
    def _post_income_reversal(cls, *, income, idempotency_key, metadata=None):
        user = income.user
        if income.account is None:
            return None, False

        asset_ledger = cls._get_or_create_account_ledger(user, income.account)
        income_ledger = cls._get_or_create_income_ledger(user, income.source, income.currency)

        lines = [
            cls._build_line(
                entry=None,
                ledger_account=income_ledger,
                direction="DEBIT",
                amount=income.amount,
                currency=income.currency,
                user=user,
            ),
            cls._build_line(
                entry=None,
                ledger_account=asset_ledger,
                direction="CREDIT",
                amount=income.amount,
                currency=income.currency,
                user=user,
                account_ref=income.account,
            ),
        ]

        return cls._create_entry(
            user=user,
            source_type="INCOME",
            source_id=income.id,
            idempotency_key=idempotency_key,
            description=f"Reversal: {income.description or income.source}",
            metadata=metadata,
            lines=lines,
            status="REVERSED",
        )

    @classmethod
    def _post_transfer_reversal(cls, *, transfer, idempotency_key, metadata=None):
        user = transfer.user
        source_ledger = cls._get_or_create_account_ledger(user, transfer.from_account)
        destination_ledger = cls._get_or_create_account_ledger(user, transfer.to_account)

        lines = [
            cls._build_line(
                entry=None,
                ledger_account=source_ledger,
                direction="DEBIT",
                amount=transfer.amount,
                currency=transfer.from_account.currency,
                user=user,
                account_ref=transfer.from_account,
            ),
            cls._build_line(
                entry=None,
                ledger_account=destination_ledger,
                direction="CREDIT",
                amount=transfer.amount,
                currency=transfer.from_account.currency,
                user=user,
                account_ref=transfer.to_account,
            ),
        ]

        return cls._create_entry(
            user=user,
            source_type="TRANSFER",
            source_id=transfer.id,
            idempotency_key=idempotency_key,
            description=f"Reversal: {transfer.description or 'Account transfer'}",
            metadata=metadata,
            lines=lines,
            status="REVERSED",
        )

    @classmethod
    def _post_loan_repayment_reversal(cls, *, repayment, idempotency_key, metadata=None):
        user = repayment.loan.user
        if repayment.from_account is None:
            return None, False

        paying_asset_ledger = cls._get_or_create_account_ledger(user, repayment.from_account)
        loan_liability_ledger = cls._get_or_create_loan_liability_ledger(user, repayment.loan)
        interest_expense_ledger = cls._get_or_create_expense_ledger(
            user,
            f"Loan Interest - {repayment.loan.name}",
            repayment.loan.currency,
        )

        lines = [
            cls._build_line(
                entry=None,
                ledger_account=paying_asset_ledger,
                direction="DEBIT",
                amount=repayment.amount,
                currency=repayment.loan.currency,
                user=user,
                account_ref=repayment.from_account,
            ),
            cls._build_line(
                entry=None,
                ledger_account=loan_liability_ledger,
                direction="CREDIT",
                amount=repayment.principal_portion,
                currency=repayment.loan.currency,
                user=user,
            ),
            cls._build_line(
                entry=None,
                ledger_account=interest_expense_ledger,
                direction="CREDIT",
                amount=repayment.interest_portion,
                currency=repayment.loan.currency,
                user=user,
            ),
        ]

        return cls._create_entry(
            user=user,
            source_type="LOAN_REPAYMENT",
            source_id=repayment.id,
            idempotency_key=idempotency_key,
            description=f"Reversal: Loan repayment for {repayment.loan.name}",
            metadata=metadata,
            lines=lines,
            status="REVERSED",
        )

    @classmethod
    def shadow_post_expense_create(cls, *, expense, version_token):
        if not cls._has_fk(expense, "account"):
            return None, False

        idempotency_key = cls._idempotency_key("EXPENSE", expense.id, f"{version_token}-POST")
        return cls.post_expense(
            expense=expense,
            idempotency_key=idempotency_key,
            metadata={"shadow_action": "CREATE", "version": version_token},
        )

    @classmethod
    def shadow_post_expense_update(cls, *, expense, previous_expense, version_token):
        cls._post_expense_reversal(
            expense=previous_expense,
            idempotency_key=cls._idempotency_key("EXPENSE", expense.id, f"{version_token}-REV"),
            metadata={"shadow_action": "UPDATE_REVERSE", "version": version_token},
        )
        if not cls._has_fk(expense, "account"):
            return None, False

        return cls.post_expense(
            expense=expense,
            idempotency_key=cls._idempotency_key("EXPENSE", expense.id, f"{version_token}-POST"),
            metadata={"shadow_action": "UPDATE_POST", "version": version_token},
        )

    @classmethod
    def shadow_post_expense_delete(cls, *, expense, version_token):
        return cls._post_expense_reversal(
            expense=expense,
            idempotency_key=cls._idempotency_key("EXPENSE", expense.id, f"{version_token}-REV"),
            metadata={"shadow_action": "DELETE_REVERSE", "version": version_token},
        )

    @classmethod
    def shadow_post_income_create(cls, *, income, version_token):
        if not cls._has_fk(income, "account"):
            return None, False

        idempotency_key = cls._idempotency_key("INCOME", income.id, f"{version_token}-POST")
        return cls.post_income(
            income=income,
            idempotency_key=idempotency_key,
            metadata={"shadow_action": "CREATE", "version": version_token},
        )

    @classmethod
    def shadow_post_income_update(cls, *, income, previous_income, version_token):
        cls._post_income_reversal(
            income=previous_income,
            idempotency_key=cls._idempotency_key("INCOME", income.id, f"{version_token}-REV"),
            metadata={"shadow_action": "UPDATE_REVERSE", "version": version_token},
        )
        if not cls._has_fk(income, "account"):
            return None, False

        return cls.post_income(
            income=income,
            idempotency_key=cls._idempotency_key("INCOME", income.id, f"{version_token}-POST"),
            metadata={"shadow_action": "UPDATE_POST", "version": version_token},
        )

    @classmethod
    def shadow_post_income_delete(cls, *, income, version_token):
        return cls._post_income_reversal(
            income=income,
            idempotency_key=cls._idempotency_key("INCOME", income.id, f"{version_token}-REV"),
            metadata={"shadow_action": "DELETE_REVERSE", "version": version_token},
        )

    @classmethod
    def shadow_post_transfer_create(cls, *, transfer, version_token):
        idempotency_key = cls._idempotency_key("TRANSFER", transfer.id, f"{version_token}-POST")
        return cls.post_transfer(
            transfer=transfer,
            idempotency_key=idempotency_key,
            metadata={"shadow_action": "CREATE", "version": version_token},
        )

    @classmethod
    def shadow_post_transfer_update(cls, *, transfer, previous_transfer, version_token):
        cls._post_transfer_reversal(
            transfer=previous_transfer,
            idempotency_key=cls._idempotency_key("TRANSFER", transfer.id, f"{version_token}-REV"),
            metadata={"shadow_action": "UPDATE_REVERSE", "version": version_token},
        )
        return cls.post_transfer(
            transfer=transfer,
            idempotency_key=cls._idempotency_key("TRANSFER", transfer.id, f"{version_token}-POST"),
            metadata={"shadow_action": "UPDATE_POST", "version": version_token},
        )

    @classmethod
    def shadow_post_transfer_delete(cls, *, transfer, version_token):
        return cls._post_transfer_reversal(
            transfer=transfer,
            idempotency_key=cls._idempotency_key("TRANSFER", transfer.id, f"{version_token}-REV"),
            metadata={"shadow_action": "DELETE_REVERSE", "version": version_token},
        )

    @classmethod
    def shadow_post_loan_repayment_create(cls, *, repayment, version_token):
        if not cls._has_fk(repayment, "from_account"):
            return None, False

        idempotency_key = cls._idempotency_key("LOAN_REPAYMENT", repayment.id, f"{version_token}-POST")
        return cls.post_loan_repayment(
            repayment=repayment,
            idempotency_key=idempotency_key,
            metadata={"shadow_action": "CREATE", "version": version_token},
        )

    @classmethod
    def shadow_post_loan_repayment_update(cls, *, repayment, previous_repayment, version_token):
        cls._post_loan_repayment_reversal(
            repayment=previous_repayment,
            idempotency_key=cls._idempotency_key("LOAN_REPAYMENT", repayment.id, f"{version_token}-REV"),
            metadata={"shadow_action": "UPDATE_REVERSE", "version": version_token},
        )
        if not cls._has_fk(repayment, "from_account"):
            return None, False

        return cls.post_loan_repayment(
            repayment=repayment,
            idempotency_key=cls._idempotency_key("LOAN_REPAYMENT", repayment.id, f"{version_token}-POST"),
            metadata={"shadow_action": "UPDATE_POST", "version": version_token},
        )

    @classmethod
    def shadow_post_loan_repayment_delete(cls, *, repayment, version_token):
        return cls._post_loan_repayment_reversal(
            repayment=repayment,
            idempotency_key=cls._idempotency_key("LOAN_REPAYMENT", repayment.id, f"{version_token}-REV"),
            metadata={"shadow_action": "DELETE_REVERSE", "version": version_token},
        )

    @staticmethod
    def _expense_like(data):
        user = Expense._meta.get_field("user").remote_field.model.objects.get(pk=data["user_id"])
        account = Account.objects.filter(pk=data.get("account_id")).first() if data.get("account_id") else None
        return SimpleNamespace(
            id=data["source_id"],
            user=user,
            amount=Decimal(data["amount"]),
            currency=data["currency"],
            category=data.get("category"),
            description=data.get("description"),
            account=account,
        )

    @staticmethod
    def _income_like(data):
        user = Income._meta.get_field("user").remote_field.model.objects.get(pk=data["user_id"])
        account = Account.objects.filter(pk=data.get("account_id")).first() if data.get("account_id") else None
        return SimpleNamespace(
            id=data["source_id"],
            user=user,
            amount=Decimal(data["amount"]),
            currency=data["currency"],
            source=data.get("source"),
            description=data.get("description"),
            account=account,
        )

    @staticmethod
    def _transfer_like(data):
        user = Transfer._meta.get_field("user").remote_field.model.objects.get(pk=data["user_id"])
        from_account = Account.objects.get(pk=data["from_account_id"])
        to_account = Account.objects.get(pk=data["to_account_id"])
        return SimpleNamespace(
            id=data["source_id"],
            user=user,
            amount=Decimal(data["amount"]),
            description=data.get("description"),
            from_account=from_account,
            to_account=to_account,
        )

    @staticmethod
    def _repayment_like(data):
        loan = Loan.objects.get(pk=data["loan_id"])
        from_account = Account.objects.filter(pk=data.get("from_account_id")).first() if data.get("from_account_id") else None
        return SimpleNamespace(
            id=data["source_id"],
            loan=loan,
            amount=Decimal(data["amount"]),
            principal_portion=Decimal(data["principal_portion"]),
            interest_portion=Decimal(data["interest_portion"]),
            from_account=from_account,
        )

    @classmethod
    def retry_shadow_failure(cls, failure):
        payload = failure.payload or {}
        handler = payload.get("handler")
        version_token = payload.get("version_token")

        if not handler or not version_token:
            raise ValidationError("Invalid retry payload.")

        if handler == "expense_create":
            expense = cls._expense_like(payload["expense"])
            cls.shadow_post_expense_create(expense=expense, version_token=version_token)
            return
        if handler == "expense_update":
            expense = cls._expense_like(payload["expense"])
            previous = cls._expense_like(payload["previous_expense"])
            cls.shadow_post_expense_update(expense=expense, previous_expense=previous, version_token=version_token)
            return
        if handler == "expense_delete":
            expense = cls._expense_like(payload["expense"])
            cls.shadow_post_expense_delete(expense=expense, version_token=version_token)
            return

        if handler == "income_create":
            income = cls._income_like(payload["income"])
            cls.shadow_post_income_create(income=income, version_token=version_token)
            return
        if handler == "income_update":
            income = cls._income_like(payload["income"])
            previous = cls._income_like(payload["previous_income"])
            cls.shadow_post_income_update(income=income, previous_income=previous, version_token=version_token)
            return
        if handler == "income_delete":
            income = cls._income_like(payload["income"])
            cls.shadow_post_income_delete(income=income, version_token=version_token)
            return

        if handler == "transfer_create":
            transfer = cls._transfer_like(payload["transfer"])
            cls.shadow_post_transfer_create(transfer=transfer, version_token=version_token)
            return
        if handler == "transfer_update":
            transfer = cls._transfer_like(payload["transfer"])
            previous = cls._transfer_like(payload["previous_transfer"])
            cls.shadow_post_transfer_update(transfer=transfer, previous_transfer=previous, version_token=version_token)
            return
        if handler == "transfer_delete":
            transfer = cls._transfer_like(payload["transfer"])
            cls.shadow_post_transfer_delete(transfer=transfer, version_token=version_token)
            return

        if handler == "loan_repayment_create":
            repayment = cls._repayment_like(payload["loan_repayment"])
            cls.shadow_post_loan_repayment_create(repayment=repayment, version_token=version_token)
            return
        if handler == "loan_repayment_update":
            repayment = cls._repayment_like(payload["loan_repayment"])
            previous = cls._repayment_like(payload["previous_loan_repayment"])
            cls.shadow_post_loan_repayment_update(repayment=repayment, previous_repayment=previous, version_token=version_token)
            return
        if handler == "loan_repayment_delete":
            repayment = cls._repayment_like(payload["loan_repayment"])
            cls.shadow_post_loan_repayment_delete(repayment=repayment, version_token=version_token)
            return

        raise ValidationError(f"Unsupported retry handler: {handler}")

    @classmethod
    def process_failure(cls, failure):
        failure.status = "RETRYING"
        failure.attempts += 1
        failure.last_attempt_at = timezone.now()
        failure.save(update_fields=["status", "attempts", "last_attempt_at", "updated_at"])

        try:
            cls.retry_shadow_failure(failure)
            failure.status = "RESOLVED"
            failure.resolved_at = timezone.now()
            failure.error_message = ""
            failure.save(update_fields=["status", "resolved_at", "error_message", "updated_at"])
        except Exception as exc:
            failure.error_message = str(exc)
            if failure.attempts >= failure.max_attempts:
                failure.status = "FAILED"
                failure.next_retry_at = None
            else:
                failure.status = "PENDING"
                backoff_minutes = min(60, 2 ** (failure.attempts - 1))
                failure.next_retry_at = timezone.now() + timedelta(minutes=backoff_minutes)
            failure.save(update_fields=["status", "error_message", "next_retry_at", "updated_at"])
            raise
