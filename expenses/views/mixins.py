from datetime import date
from decimal import Decimal
from django.utils import timezone
from ..models import Expense, Income, Transfer, RecurringTransaction, UserProfile
from ..utils import get_exchange_rate

class RecurringTransactionMixin:
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            process_user_recurring_transactions(request.user)
        return super().dispatch(request, *args, **kwargs)

    def process_recurring_transactions(self, user):
        # Deprecated: Use process_user_recurring_transactions instead
        process_user_recurring_transactions(user)

def process_user_recurring_transactions(user):
    today = date.today()
    profile = user.profile
    recurring_txs = RecurringTransaction.objects.filter(user=user, is_active=True).order_by('created_at')
    
    # print(f"DEBUG: Processing RTs for {user.username}. Total active: {recurring_txs.count()}")
    
    # Enforce Tier Limits for processing
    if not profile.is_pro:
        # Technical Improvement: allow 3 for Plus, 0 for Free
        limit = 3 if profile.is_plus else 0
        recurring_txs = recurring_txs[:limit]
    
    # print(f"DEBUG: Limit applied: {limit if not profile.is_pro else 'Unlimited'}. RTs to process: {len(recurring_txs)}")

    new_expenses = []
    new_incomes = []
    updates_needed = []
    
    try:
        base_currency = user.profile.currency
    except UserProfile.DoesNotExist:
        return

    for rt in recurring_txs:
        if not rt.last_processed_date:
            current_date = rt.start_date
        else:
            current_date = rt.get_next_date(rt.last_processed_date, rt.frequency)

        # print(f"DEBUG: RT {rt.description} ({rt.transaction_type}). Next due: {current_date}, Today: {today}")

        if current_date > today:
            continue

        exchange_rate = Decimal('1.0')
        if rt.currency != base_currency:
            exchange_rate = get_exchange_rate(rt.currency, base_currency)
        
        base_amount = (rt.amount * exchange_rate).quantize(Decimal('0.01'))

        while current_date <= today:
            description = f"{rt.description} (Recurring)"
            # print(f"DEBUG: Creating {rt.transaction_type} for {current_date}")
            
            if rt.transaction_type == 'EXPENSE':
                category = rt.category or 'Uncategorized'
                exists = Expense.objects.filter(user=user, date=current_date, amount=rt.amount, description=description, currency=rt.currency, category=category).exists()
                if not exists:
                    new_expenses.append(Expense(user=user, date=current_date, amount=rt.amount, currency=rt.currency, category=category, description=description, payment_method=rt.payment_method, exchange_rate=exchange_rate, base_amount=base_amount))
            elif rt.transaction_type == 'TRANSFER':
                # Create actual Transfer records that update account balances
                if rt.from_account and rt.to_account:
                    exists = Transfer.objects.filter(
                        user=user, date=current_date, amount=rt.amount,
                        from_account=rt.from_account, to_account=rt.to_account,
                        description=description
                    ).exists()
                    if not exists:
                        # Transfer.save() handles atomic balance updates, so save individually
                        Transfer(
                            user=user, date=current_date, amount=rt.amount,
                            from_account=rt.from_account, to_account=rt.to_account,
                            description=description
                        ).save()
            else:
                source = rt.source or 'Other'
                # Unique constraint for Income: user, date, amount, currency, source
                exists = Income.objects.filter(user=user, date=current_date, amount=rt.amount, currency=rt.currency, source=source).exists()
                if not exists:
                    new_incomes.append(Income(user=user, date=current_date, amount=rt.amount, currency=rt.currency, source=source, description=description, exchange_rate=exchange_rate, base_amount=base_amount))
            
            rt.last_processed_date = current_date
            current_date = rt.get_next_date(current_date, rt.frequency)
        
        updates_needed.append(rt)

    if new_expenses:
        # print(f"DEBUG: Bulk creating {len(new_expenses)} expenses")
        Expense.objects.bulk_create(new_expenses)
    if new_incomes:
        # print(f"DEBUG: Bulk creating {len(new_incomes)} incomes")
        Income.objects.bulk_create(new_incomes)
    if updates_needed:
        RecurringTransaction.objects.bulk_update(updates_needed, ['last_processed_date'])
