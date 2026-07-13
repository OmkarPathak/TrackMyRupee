import logging
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.core.cache import cache
from django.db.models import Sum

from ..models import (
    CapitalEvent,
    Expense,
    Income,
    LoanRepayment,
    RecurringTransaction,
    Transfer,
    UserProfile,
)
from ..utils import get_exchange_rate
from .utils import get_object_by_uuid_or_pk, redirect_to_uuid_url_if_needed

logger = logging.getLogger(__name__)


class RecurringTransactionMixin:
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            process_user_recurring_transactions(request.user)
        return super().dispatch(request, *args, **kwargs)

    def process_recurring_transactions(self, user):
        # Deprecated: Use process_user_recurring_transactions instead
        process_user_recurring_transactions(user)

def process_user_recurring_transactions(user):
    if not user.is_authenticated:
        return
    today = date.today()

    # Skip if already processed today for this user (prevents redundant writes on every page load)
    cooldown_key = f'recurring_processed_{user.id}_{today}'
    if cache.get(cooldown_key):
        return
    cache.set(cooldown_key, True, 86400)  # lock for 24 hours

    profile = user.profile
    recurring_txs = RecurringTransaction.objects.filter(user=user, is_active=True).select_related('account', 'from_account', 'to_account', 'loan').order_by('created_at')
    
    # Enforce Tier Limits for processing
    from finance_tracker.plans import get_limit
    limit = get_limit(profile.active_tier, 'recurring_transactions')
    if limit != -1:
        recurring_txs = recurring_txs[:limit]

    updates_needed = []
    loan_principal_paid_map = {}
    
    try:
        base_currency = user.profile.currency
    except UserProfile.DoesNotExist:
        return

    loan_ids = [rt.loan_id for rt in recurring_txs if rt.transaction_type == 'LOAN' and rt.loan_id]
    if loan_ids:
        repayment_totals = (
            LoanRepayment.objects.filter(loan_id__in=loan_ids)
            .values('loan_id')
            .annotate(total_principal=Sum('principal_portion'))
        )
        loan_principal_paid_map = {
            row['loan_id']: Decimal(str(row['total_principal'] or 0))
            for row in repayment_totals
        }
        # Add capital event prepayments so recurring EMI scheduling sees the correct balance
        capital_prepayment_totals = (
            CapitalEvent.objects
            .filter(linked_loan_id__in=loan_ids, subtype__in=['loan_down_payment', 'loan_prepayment'])
            .values('linked_loan_id')
            .annotate(total_prepaid=Sum('amount'))
        )
        for row in capital_prepayment_totals:
            lid = row['linked_loan_id']
            loan_principal_paid_map[lid] = (
                loan_principal_paid_map.get(lid, Decimal('0.00'))
                + Decimal(str(row['total_prepaid'] or 0))
            )

    for rt in recurring_txs:
        if not rt.last_processed_date:
            current_date = rt.start_date
        else:
            current_date = rt.get_next_date(rt.last_processed_date, rt.frequency)

        if rt.end_date and current_date > rt.end_date:
            rt.is_active = False
            rt.save(update_fields=['is_active'])
            continue

        if current_date > today:
            continue

        exchange_rate = Decimal('1.0')
        if rt.currency != base_currency:
            exchange_rate = get_exchange_rate(rt.currency, base_currency)
        
        base_amount = (rt.amount * exchange_rate).quantize(Decimal('0.01'))

        deactivated = False
        while current_date <= today:
            if rt.end_date and current_date > rt.end_date:
                rt.is_active = False
                deactivated = True
                break

            description = f"{rt.description} (Recurring)"
            posted_successfully = False
            
            if rt.transaction_type == 'EXPENSE':
                category = rt.category or 'Uncategorized'
                exists = Expense.objects.filter(user=user, date=current_date, amount=rt.amount, description=description, currency=rt.currency, category=category).exists()
                if not exists:
                    try:
                        Expense(
                            user=user, date=current_date, amount=rt.amount,
                            currency=rt.currency, category=category,
                            description=description, payment_method=rt.payment_method,
                            exchange_rate=exchange_rate, base_amount=base_amount,
                            account=rt.account,
                        ).save()
                        posted_successfully = True
                    except Exception as exc:
                        print("EXCEPTION DURING RECURRING EXPENSE POSTING:", repr(exc))
                        logger.warning("Recurring expense posting failed", exc_info=exc)
                        break
                else:
                    posted_successfully = True

            elif rt.transaction_type == 'TRANSFER':
                if rt.from_account and rt.to_account:
                    # Transfers always use the from_account's currency as primary
                    exists = Transfer.objects.filter(
                        user=user, date=current_date, amount=rt.amount,
                        from_account=rt.from_account, to_account=rt.to_account,
                        description=description
                    ).exists()
                    if not exists:
                        try:
                            # Transfer model's save() handles balance and currency logic
                            new_transfer = Transfer(
                                user=user, date=current_date, amount=rt.amount,
                                from_account=rt.from_account, to_account=rt.to_account,
                                description=description
                            )
                            new_transfer.save()
                            posted_successfully = True
                        except Exception as e:
                            logger.warning("Recurring transfer posting failed", exc_info=e)
                            break 
                    else:
                        posted_successfully = True

            elif rt.transaction_type == 'LOAN':
                if rt.loan and rt.account:
                    principal_paid = loan_principal_paid_map.get(rt.loan_id, Decimal('0.00'))
                    remaining_principal = (Decimal(str(rt.loan.initial_principal)) - principal_paid).quantize(Decimal('0.01'))
                    if remaining_principal <= 0:
                        posted_successfully = True
                    else:
                        latest_rate_obj = rt.loan.interest_rates.order_by('-effective_date').first()
                        annual_rate = Decimal(str(latest_rate_obj.interest_rate)) if latest_rate_obj else Decimal('0.00')

                        period_days_map = {
                            'DAILY': Decimal('1'),
                            'WEEKLY': Decimal('7'),
                            'MONTHLY': Decimal('30'),
                            'YEARLY': Decimal('365'),
                        }
                        period_days = period_days_map.get(rt.frequency, Decimal('30'))
                        interest_payment = (
                            remaining_principal * annual_rate * period_days / Decimal('36500')
                        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

                        repayment_amount = Decimal(str(rt.amount)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                        principal_payment = (repayment_amount - interest_payment).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

                        if principal_payment <= 0:
                            logger.warning("Recurring loan repayment amount is too low to cover interest for loan %s", rt.loan_id)
                            break

                        if principal_payment > remaining_principal:
                            principal_payment = remaining_principal.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                            repayment_amount = (principal_payment + interest_payment).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

                        exists = LoanRepayment.objects.filter(
                            loan=rt.loan,
                            date=current_date,
                            from_account=rt.account,
                        ).exists()
                        if not exists:
                            try:
                                LoanRepayment.objects.create(
                                    loan=rt.loan,
                                    from_account=rt.account,
                                    date=current_date,
                                    amount=repayment_amount,
                                    principal_portion=principal_payment,
                                    interest_portion=interest_payment,
                                )
                                loan_principal_paid_map[rt.loan_id] = principal_paid + principal_payment
                                posted_successfully = True
                            except Exception as exc:
                                logger.warning("Recurring loan repayment posting failed", exc_info=exc)
                                break
                        else:
                            posted_successfully = True

            elif rt.transaction_type == 'CAPITAL':
                subtype = rt.capital_subtype or 'other'
                exists = CapitalEvent.objects.filter(
                    user=user, date=current_date, amount=rt.amount, currency=rt.currency, subtype=subtype
                ).exists()
                if not exists:
                    try:
                        CapitalEvent(
                            user=user, date=current_date, amount=rt.amount,
                            currency=rt.currency, subtype=subtype,
                            note=description, account=rt.account,
                            linked_loan=rt.loan,
                            exclude_from_averages=rt.exclude_from_averages,
                            exclude_from_budget=rt.exclude_from_budget,
                            include_in_net_worth=rt.include_in_net_worth,
                        ).save()
                        posted_successfully = True
                    except Exception as exc:
                        logger.warning("Recurring capital event posting failed", exc_info=exc)
                        break
                else:
                    posted_successfully = True

            else:
                source = rt.source or 'Other'
                exists = Income.objects.filter(user=user, date=current_date, amount=rt.amount, currency=rt.currency, source=source).exists()
                if not exists:
                    try:
                        Income(
                            user=user, date=current_date, amount=rt.amount,
                            currency=rt.currency, source=source,
                            description=description, exchange_rate=exchange_rate,
                            base_amount=base_amount, account=rt.account,
                        ).save()
                        posted_successfully = True
                    except Exception as exc:
                        logger.warning("Recurring income posting failed", exc_info=exc)
                        break
                else:
                    posted_successfully = True

            if not posted_successfully:
                break
            
            rt.last_processed_date = current_date
            current_date = rt.get_next_date(current_date, rt.frequency)
        
        if deactivated:
            rt.save()
        else:
            updates_needed.append(rt)

    if updates_needed:
        RecurringTransaction.objects.bulk_update(updates_needed, ['last_processed_date'])


class UUIDOrIntLookupMixin:
    def get_object(self, queryset=None):
        if queryset is None:
            queryset = self.get_queryset()
        pk = self.kwargs.get(self.pk_url_kwarg or 'pk')
        
        return get_object_by_uuid_or_pk(queryset, pk)

    def dispatch(self, request, *args, **kwargs):
        if request.method == 'GET' and (self.pk_url_kwarg or 'pk') in kwargs:
            try:
                obj = self.get_object()
                
                redirect_response = redirect_to_uuid_url_if_needed(request, obj, lookup_kwarg=self.pk_url_kwarg or 'pk')
                if redirect_response:
                    return redirect_response
            except Exception:
                pass
        return super().dispatch(request, *args, **kwargs)
