"""Work out and store every active person's insights. Safe to run again at any time: rows are updated in place and
a dismissal or snooze is never undone. Run nightly (see the Insights README). Reports counts only."""
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand
from django.utils import timezone

from expenses.insights import engine

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Generate and store Insights Engine results for active users (idempotent, batched).'

    def add_arguments(self, parser):
        parser.add_argument('--user-id', type=int, help='Only this user')
        parser.add_argument('--batch-size', type=int, default=200)
        parser.add_argument('--active-days', type=int, default=60, help='Users who signed in within this many days')
        parser.add_argument('--light-only', action='store_true', help='Skip the heavy rules')

    def handle(self, *args, **options):
        users = User.objects.filter(is_active=True).select_related('profile')
        if options['user_id']:
            users = users.filter(pk=options['user_id'])
        else:
            users = users.filter(last_login__gte=timezone.now() - timedelta(days=options['active_days']))
            if settings.INSIGHTS_ENGINE_MODE != 'on':
                users = users.filter(pk__in=settings.INSIGHTS_ENGINE_USER_IDS)
        costs = ('light',) if options['light_only'] else ('light', 'heavy')
        today = timezone.localdate()
        done = failed = written = 0
        for user in users.order_by('pk').iterator(chunk_size=options['batch_size']):
            try:
                result = engine.refresh(user, today=today, costs=costs, profile=user.profile)
            except ObjectDoesNotExist:
                continue
            except Exception as exc:        # noqa: BLE001 - one person's failure never stops the batch
                failed += 1
                logger.warning('insight refresh failed type=%s', type(exc).__name__)
                continue
            done += 1
            written += result.written
        self.stdout.write(f'insights generated users={done} rows={written} failed={failed}')
