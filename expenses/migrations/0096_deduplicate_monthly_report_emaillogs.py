from django.db import migrations


def deduplicate_monthly_report_emails(apps, schema_editor):
    """
    Remove duplicate EmailLog entries created for monthly financial reports.
    Keeps the first (lowest ID) of each (user_id, to_email, subject, status) combination.
    """
    EmailLog = apps.get_model('expenses', 'EmailLog')
    seen = set()
    to_delete = []

    for log in EmailLog.objects.filter(subject__startswith='Your Monthly Financial Report', status='SENT').order_by('id'):
        key = (log.user_id, log.to_email, log.subject, log.status)
        if key in seen:
            to_delete.append(log.id)
        else:
            seen.add(key)

    if to_delete:
        EmailLog.objects.filter(id__in=to_delete).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0095_expense_expense_user_date_idx'),
    ]

    operations = [
        migrations.RunPython(deduplicate_monthly_report_emails, migrations.RunPython.noop),
    ]
