import logging

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from expenses.views.mixins import process_user_recurring_transactions

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Processes recurring transactions for all active users.'

    def add_arguments(self, parser):
        parser.add_argument('--user-id', type=int, help='Process recurring transactions for a specific user ID')

    def handle(self, *args, **options):
        user_id = options.get('user_id')
        if user_id:
            users = User.objects.filter(id=user_id, is_active=True)
        else:
            users = User.objects.filter(is_active=True)

        count = 0
        for user in users:
            try:
                process_user_recurring_transactions(user)
                count += 1
            except Exception as exc:
                logger.error("Failed to process recurring transactions for user %s: %s", user.id, exc, exc_info=True)
                self.stderr.write(f"Error processing user {user.username}: {exc}")

        self.stdout.write(self.style.SUCCESS(f"Processed recurring transactions for {count} users."))
