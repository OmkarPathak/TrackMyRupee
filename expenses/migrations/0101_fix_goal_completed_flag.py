from django.db import migrations
from django.db.models import F, Q


def fix_completed_flag(apps, schema_editor):
    """The flag was recomputed in memory but never saved when a contribution changed a goal, so
    goals could read "open" after reaching their target (or "done" after money was removed).
    Recompute it once from the amounts."""
    SavingsGoal = apps.get_model('expenses', 'SavingsGoal')
    reached = Q(target_amount__gt=0, current_amount__gte=F('target_amount'))
    SavingsGoal.objects.filter(reached, is_completed=False).update(is_completed=True)
    SavingsGoal.objects.exclude(reached).filter(is_completed=True).update(is_completed=False)


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0100_expense_keyword_hint'),
    ]

    operations = [
        migrations.RunPython(fix_completed_flag, migrations.RunPython.noop),
    ]
