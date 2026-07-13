# Django Finance Tracker - Codebase Exploration Summary

## Overview
A comprehensive Django-based personal finance tracking application with multi-currency support, expense management, income tracking, loan management, and advanced analytics.

---

## 1. USER PROFILE MODEL STRUCTURE

### Current Profile Model
**Location:** [expenses/models.py](expenses/models.py#L952)

```python
class UserProfile(models.Model):
    user = OneToOneField(User)
    currency = CharField(default='₹')                    # Base currency
    language = CharField(choices=['en', 'hi', 'mr'])     # Multi-language
    has_seen_tutorial = BooleanField()
    
    # Subscription fields
    tier = CharField(choices=['FREE', 'PLUS', 'PRO'])
    subscription_end_date = DateTimeField(null=True)
    is_lifetime = BooleanField()
    
    # Push notification preferences
    daily_reminder = BooleanField(default=True)
```

**Key Properties:**
- `is_pro` - Check if user has active Pro subscription
- `is_plus` - Check if user has Plus or higher tier
- `active_tier` - Returns actual active tier respecting subscription expiry
- `can_add_account()`, `can_add_expense()`, `can_add_recurring()` - Tier-based limits

### Current Profile Settings Workflow

**View:** [expenses/views/settings.py](expenses/views/settings.py) - `ProfileUpdateView`
- Template: [templates/expenses/profile_settings.html](templates/expenses/profile_settings.html)
- Form: [expenses/forms.py](expenses/forms.py#L256) - `ProfileUpdateForm`

**Current Fields Managed:**
- First Name / Last Name
- Email Address
- Daily Expense Reminder (toggle)
- Social login detection

**Related Settings Views:**
- `CurrencyUpdateView` - Updates `UserProfile.currency`
- `LanguageUpdateView` - Updates `UserProfile.language`

---

## 2. MODELS RELATED TO USERS & EXPENSES

### Core Transaction Models

#### Expense Model
**Location:** [expenses/models.py](expenses/models.py#L300+)
```python
class Expense(models.Model):
    user = ForeignKey(User)
    date = DateField()
    amount = DecimalField(max_digits=10, decimal_places=2)
    description = TextField()
    category = CharField(max_length=255)
    payment_method = CharField(choices=['Cash', 'Credit Card', 'Debit Card', 'UPI', 'NetBanking'])
    account = ForeignKey(Account, null=True, blank=True)
    currency = CharField(max_length=5, default='₹')
    exchange_rate = DecimalField()
    base_amount = DecimalField()  # Amount in user's base currency
```

#### Income Model
**Location:** [expenses/models.py](expenses/models.py#L482)
```python
class Income(models.Model):
    user = ForeignKey(User)
    date = DateField()
    amount = DecimalField(max_digits=10, decimal_places=2)
    source = CharField(max_length=255)  # e.g., "Salary", "Freelance", "Dividend"
    description = TextField()
    account = ForeignKey(Account, null=True, blank=True)
    currency = CharField(max_length=5, default='₹')
    exchange_rate = DecimalField()
    base_amount = DecimalField()
```

#### Account Model
**Location:** [expenses/models.py](expenses/models.py#L103)
```python
class Account(models.Model):
    user = ForeignKey(User)
    name = CharField(max_length=100)
    account_type = CharField(choices=['CASH', 'BANK', 'CREDIT_CARD', 'INVESTMENT', 'FIXED_DEPOSIT', 'OTHER'])
    balance = DecimalField()
    currency = CharField(max_length=5, default='₹')
    is_active = BooleanField(default=True)
```

#### RecurringTransaction Model
**Location:** [expenses/models.py](expenses/models.py#L800+)
```python
class RecurringTransaction(models.Model):
    user = ForeignKey(User)
    transaction_type = CharField(choices=['EXPENSE', 'INCOME', 'TRANSFER', 'LOAN'])
    amount = DecimalField()
    description = TextField()
    frequency = CharField(choices=['DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY'])
    start_date = DateField()
    last_processed_date = DateField()
    is_active = BooleanField(default=True)
    
    # Source/Category fields for different transaction types
    category = CharField()      # For EXPENSE
    source = CharField()        # For INCOME
    account = ForeignKey(Account)
    from_account = ForeignKey(Account)  # For TRANSFER
    to_account = ForeignKey(Account)    # For TRANSFER
    loan = ForeignKey('Loan')           # For LOAN repayment
```

#### Category Model
**Location:** [expenses/models.py](expenses/models.py#L459)
```python
class Category(models.Model):
    user = ForeignKey(User)
    name = CharField(max_length=255)
    icon = CharField(max_length=50, default='bi-tag')
    limit = DecimalField(null=True, blank=True)  # Monthly budget limit
```

---

## 3. DASHBOARD ANALYSIS CALCULATION

### Current Dashboard Approach
**Location:** [expenses/views/dashboard.py](expenses/views/dashboard.py#L1)

#### Key Metrics Calculated:

**1. Net Worth History**
```python
net_worth_history = FinancialService.get_monthly_history(request.user, 6)
# Returns: [{'month': date, 'income': float, 'expense': float, 'savings': float}]
```

**2. Expense Analysis**
- **Category Breakdown:** Group expenses by category, calculate % of total, project month-end
- **Time Trends:** Daily or Monthly aggregation with 7-day rolling average
- **Top Expenses:** Top 5 expenses by amount
- **Budget Status:** Compare category spending vs monthly limits

**3. Income vs Expense Trend**
```python
# Merged view of income, expenses, and loan interest over time
ie_income_data = [income for each period]
ie_expense_data = [expenses + loan_interest for each period]
ie_savings_data = [income - expenses - loan_principal for each period]
```

**4. Salary Breakdown Analysis** ("Where Did My Salary Go?")
**Location:** [expenses/views/dashboard.py](expenses/views/dashboard.py#L741-L950)

The dashboard calculates:
- **Total Income** - Sum of all income transactions
- **Total Expenses** - Sum of all expense transactions
- **Savings Rate** - (Savings / Total Income) × 100
- **Daily Burn Rate** - Total Expenses / Days Elapsed in month
- **Top Categories** - Top 3 spending categories
- **Spending Pace** - Projected month-end spending vs budget

```python
salary_breakdown = {
    'income': total_income,
    'expenses': total_expenses,
    'savings': savings,
    'savings_rate': savings_rate,
    'daily_burn': daily_burn,
    'top_categories': category_limits[:3],
    'month_name': display_month,
    'spending_pace': {
        'daily_spending_pace': daily_burn,
        'projected_month_spend': daily_burn * num_days,
        'spent_percent': (total_expenses / total_monthly_budget) * 100,
        'ideal_percent': (days_elapsed / num_days) * 100,
    }
}
```

#### Dashboard Filters
Users can filter by:
- **Date Range:** Custom start/end dates
- **Year/Month:** Single or multiple months
- **Category:** Single or multiple expense categories

#### AI Insights Generated
- Budget breach warnings
- Category overspending alerts
- 3-month average spending comparisons
- "Relatable metrics" (e.g., "equivalent to X Netflix subscriptions")

---

## 4. EXISTING SALARY-RELATED LOGIC

### Current Implementation:

**1. Income Model has `source` field**
- Text field for income source (e.g., "Salary", "Freelance", "Dividend")
- Used to identify salary income but not to automate salary date logic

**2. RecurringTransaction Model**
- Already supports MONTHLY frequency for recurring income
- Has `start_date` and `last_processed_date` fields
- Example: Salary could be set as recurring monthly income
- **Code location:** [expenses/models.py](expenses/models.py#L800+)

```python
# Example: Salary can be tracked as recurring transaction
RecurringTransaction.objects.create(
    user=user,
    transaction_type='INCOME',
    amount=Decimal('5000'),
    source='Salary',
    frequency='MONTHLY',
    start_date=date(2026, 5, 25),  # Salary date each month
)
```

**3. Income Forecasting Service**
**Location:** [expenses/services.py](expenses/services.py)

Current `FinancialService` provides:
- `get_monthly_history()` - Aggregates income/expense by month
- `get_categorical_spending()` - Breaks down spending by category
- `get_spending_streak()` - Calculates consecutive overspend days

---

## 5. KEY FILES FOR SALARY DATE FEATURE

### Models to Modify:
- ✅ [expenses/models.py](expenses/models.py#L952) - **UserProfile** (add salary_date field)
- ✅ [expenses/models.py](expenses/models.py#L482) - **Income** (already has source)

### Views to Update:
- ✅ [expenses/views/settings.py](expenses/views/settings.py#L29) - **ProfileUpdateView** & **CurrencyUpdateView**
- ✅ [expenses/views/dashboard.py](expenses/views/dashboard.py#L741) - Dashboard salary breakdown logic

### Forms to Update:
- ✅ [expenses/forms.py](expenses/forms.py#L256) - **ProfileUpdateForm**

### Templates to Update:
- ✅ [templates/expenses/profile_settings.html](templates/expenses/profile_settings.html)
- Possibly [templates/expenses/currency_settings.html](templates/expenses/currency_settings.html)

### Services to Enhance:
- ✅ [expenses/services.py](expenses/services.py) - Add salary-based analysis methods

---

## 6. RECOMMENDED ARCHITECTURE FOR SALARY DATE FEATURE

### Database Addition:
```python
class UserProfile(models.Model):
    # ... existing fields ...
    salary_date = IntegerField(null=True, blank=True)  # Day of month (1-31)
    salary_source = CharField(max_length=255, blank=True)  # e.g., "Salary", "Freelance"
```

### Dashboard Enhancement:
- Filter dashboard to show spending between salary dates
- "Salary Cycle" view: Show spending from salary_date to salary_date
- Alerts: Warn if spending exceeds salary before next salary date

### Settings UI Changes:
- Add "Salary Date" field to Profile Settings
- Add "Salary Source" field (for identifying salary income)
- Example: "I receive salary on 25th of each month"

### Analysis Enhancements:
- "Income vs Expense per Salary Cycle" (not calendar month)
- "Savings % of salary" (more actionable than % of total income)
- "Days until next salary" indicator

---

## 7. CURRENCY & MULTI-LANGUAGE SUPPORT

### Implemented:
- ✅ **Multi-currency** - User base currency stored in UserProfile
- ✅ **Exchange rates** - get_exchange_rate() utility converts transactions
- ✅ **Currency choices** - ₹, $, €, £, ¥, A$, C$, CHF, 元, ₩
- ✅ **Multi-language** - en, hi (Hindi), mr (Marathi)

### Relevant Files:
- [expenses/utils.py](expenses/utils.py) - `get_exchange_rate()` function
- [expenses/models.py](expenses/models.py#L13-L24) - CURRENCY_CHOICES
- [expenses/context_processors.py](expenses/context_processors.py) - Currency formatting for templates

---

## 8. MIGRATION PLANNING

### Step 1: Add Model Field
Add `salary_date` and `salary_source` to UserProfile

### Step 2: Create Forms & Views
- Update ProfileUpdateForm
- Update ProfileUpdateView

### Step 3: Update Template
- Add salary settings section to profile_settings.html

### Step 4: Enhance Dashboard
- Add salary cycle calculations
- Update salary breakdown logic
- Add salary-based insights

### Step 5: Add Services
- Create `SalaryAnalysisService` in services.py
- Methods for salary cycle aggregation and forecasting

---

## Summary of File Locations

| Component | File Path |
|-----------|-----------|
| **User Profile Model** | [expenses/models.py](expenses/models.py#L952) |
| **Income Model** | [expenses/models.py](expenses/models.py#L482) |
| **Expense Model** | [expenses/models.py](expenses/models.py#L300) |
| **RecurringTransaction Model** | [expenses/models.py](expenses/models.py#L800) |
| **Dashboard View** | [expenses/views/dashboard.py](expenses/views/dashboard.py) |
| **Settings View** | [expenses/views/settings.py](expenses/views/settings.py) |
| **Profile Update Form** | [expenses/forms.py](expenses/forms.py#L256) |
| **Profile Settings Template** | [templates/expenses/profile_settings.html](templates/expenses/profile_settings.html) |
| **Financial Services** | [expenses/services.py](expenses/services.py) |
| **Utilities** | [expenses/utils.py](expenses/utils.py) |

---

## Current Dashboard Features

✅ **Expense Tracking** - By date, category, amount, payment method  
✅ **Income Tracking** - By source, date, amount  
✅ **Budget Management** - Category-wise monthly limits  
✅ **Account Management** - Multiple accounts with currency support  
✅ **Recurring Transactions** - Daily, Weekly, Monthly, Yearly  
✅ **Loan Tracking** - EMI calculations with interest tracking  
✅ **AI Insights** - Category trends, budget alerts, savings goals  
✅ **Net Worth Tracking** - 6-month history  
✅ **Multi-currency Support** - Currency conversion with exchange rates  
✅ **Advanced Filters** - By date range, year/month, category  

✅ **Salary Breakdown Analysis** - "Where Did My Salary Go?" visualization  
⚠️ **Salary Date Automation** - PARTIALLY implemented (recurring transactions only)

---

## Next Steps for Salary Date Feature

1. **Add `salary_date` field** to UserProfile model
2. **Create migration** for database schema change
3. **Update ProfileUpdateForm** to include salary date input
4. **Enhance ProfileUpdateView** to handle salary date
5. **Update profile_settings.html** template
6. **Create SalaryAnalysisService** in services.py
7. **Update dashboard.py** to use salary date for period calculations
8. **Add salary cycle visualizations** to dashboard templates
