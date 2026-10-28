from django.db import migrations, models
from django.db.models import F
import django.utils.timezone


def backfill(apps, schema_editor):
    apps.get_model('expenses', 'FamilyShare').objects.update(amounts_since=F('created_at'))


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0128_emergency_access'),
    ]

    operations = [
        migrations.AddField(
            model_name='familyshare',
            name='amounts_since',
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
