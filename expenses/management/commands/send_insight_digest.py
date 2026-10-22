"""Email the insight digest to Pro people who have it switched on: one email a week (or a day, by choice), only when
there is something worth saying, never twice for the same insight. Which channels may send at all is set by
INSIGHTS_CHANNELS (it must include "email"). Safe to run again. Reports counts only."""
import logging

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from expenses.insights import channel_enabled
from expenses.insights.adapters import digest
from expenses.models import InsightState

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Email the insight digest (Pro): capped, idempotent, batched.'

    def add_arguments(self, parser):
        parser.add_argument('--user-id', type=int)
        parser.add_argument('--batch-size', type=int, default=200)

    def handle(self, *args, **options):
        if not channel_enabled('email'):
            self.stdout.write('the insight digest is off (INSIGHTS_CHANNELS does not include email)')
            return
        now = timezone.now()
        today = timezone.localdate(now)
        pending = InsightState.objects.filter(valid_until__gte=today, dismissed_at__isnull=True, acted_at__isnull=True,
                                              locked=False, email_at__isnull=True)
        if options['user_id']:
            pending = pending.filter(user_id=options['user_id'])
        ids = sorted(set(pending.values_list('user_id', flat=True)))
        sent = failed = 0
        for start in range(0, len(ids), options['batch_size']):
            chunk = ids[start:start + options['batch_size']]
            for user in User.objects.filter(pk__in=chunk, is_active=True).exclude(email='').select_related('profile'):
                try:
                    sent += digest.send(user, user.profile, now)
                except Exception as exc:    # noqa: BLE001
                    failed += 1
                    logger.warning('insight digest failed type=%s', type(exc).__name__)
        self.stdout.write(f'insight digests sent={sent} failed={failed}')
