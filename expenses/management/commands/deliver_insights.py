"""Send the day's insights to the bell and, when allowed, as push. Which channels may send at all is set by
INSIGHTS_CHANNELS. Safe to run again: each row is claimed once per channel. Reports counts only."""
import logging

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from expenses.insights import channel_enabled
from expenses.insights.adapters import bell
from expenses.models import InsightState

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Deliver stored insights to the bell and push (capped, idempotent, batched).'

    def add_arguments(self, parser):
        parser.add_argument('--user-id', type=int)
        parser.add_argument('--batch-size', type=int, default=200)

    def handle(self, *args, **options):
        if not (channel_enabled('bell') or channel_enabled('push')):
            self.stdout.write('insights delivery is off (INSIGHTS_CHANNELS is empty)')
            return
        from webpush.models import PushInformation

        now = timezone.now()
        today = timezone.localdate(now)
        pending = InsightState.objects.filter(valid_until__gte=today, dismissed_at__isnull=True, acted_at__isnull=True,
                                              locked=False)
        if options['user_id']:
            pending = pending.filter(user_id=options['user_id'])
        ids = sorted(set(pending.filter(bell_at__isnull=True).values_list('user_id', flat=True))
                     | set(pending.filter(push_at__isnull=True).values_list('user_id', flat=True)))
        bells = pushes = 0
        for start in range(0, len(ids), options['batch_size']):
            chunk = ids[start:start + options['batch_size']]
            with_push = set(PushInformation.objects.filter(user_id__in=chunk).values_list('user_id', flat=True))
            for user in User.objects.filter(pk__in=chunk, is_active=True).select_related('profile'):
                try:
                    bells += bell.deliver_bell(user, user.profile, now)
                    pushes += bell.deliver_push(user, user.profile, now, has_push=user.pk in with_push)
                except Exception as exc:    # noqa: BLE001
                    logger.warning('insight delivery failed type=%s', type(exc).__name__)
        self.stdout.write(f'insights delivered bell={bells} push={pushes}')
