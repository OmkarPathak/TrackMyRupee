from django.db import migrations

def convert_reversed_to_posted(apps, schema_editor):
    JournalEntry = apps.get_model('expenses', 'JournalEntry')
    JournalEntry.objects.filter(status='REVERSED').update(status='POSTED')

class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0055_add_salary_date_to_userprofile'),
    ]

    operations = [
        migrations.RunPython(convert_reversed_to_posted, reverse_code=migrations.RunPython.noop),
    ]
