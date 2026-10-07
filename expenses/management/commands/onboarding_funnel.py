from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.db.models import Count, Min
from django.utils import timezone
from expenses.models import Expense, OnboardingEvent, OnboardingState, UserProfile


class Command(BaseCommand):
    help = 'Computes Onboarding Funnel drop-off and activation rates split by signup cohort.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--since',
            type=str,
            default=None,
            help='Start date in YYYY-MM-DD format (default: all time)',
        )

    def handle(self, *args, **options):
        since_str = options.get('since')
        since_dt = None
        if since_str:
            try:
                since_dt = timezone.make_aware(datetime.strptime(since_str, '%Y-%m-%d'))
            except Exception:
                self.stderr.write(f"Invalid date format: {since_str}. Use YYYY-MM-DD.")
                return

        events_qs = OnboardingEvent.objects.all()
        if since_dt:
            events_qs = events_qs.filter(created_at__gte=since_dt)

        self.stdout.write("==========================================")
        self.stdout.write("       TRACKMYRUPEE ONBOARDING FUNNEL     ")
        self.stdout.write("==========================================")

        # 1. Funnel Steps
        steps = [
            ('onboarding_started', 'Started Onboarding'),
            ('onboarding_step_viewed_1', 'Viewed Step 1'),
            ('onboarding_step_completed_1', 'Completed Step 1'),
            ('onboarding_step_skipped_1', 'Skipped Step 1'),
            ('onboarding_step_viewed_2', 'Viewed Step 2'),
            ('onboarding_step_completed_2', 'Completed Step 2'),
            ('onboarding_step_skipped_2', 'Skipped Step 2'),
            ('onboarding_step_viewed_3', 'Viewed Step 3'),
            ('onboarding_step_completed_3', 'Completed Step 3'),
            ('onboarding_step_skipped_3', 'Skipped Step 3'),
            ('onboarding_completed', 'Finished Onboarding'),
        ]

        started_users = set(
            events_qs.filter(event='onboarding_started').values_list('user_id', flat=True).distinct()
        )
        total_started = len(started_users)
        self.stdout.write(f"Total Users Started: {total_started}\n")

        for event_key, label in steps:
            if '_viewed_' in event_key:
                step_num = int(event_key.split('_')[-1])
                user_ids = set(
                    events_qs.filter(event='onboarding_step_viewed', properties__step=step_num)
                    .values_list('user_id', flat=True)
                    .distinct()
                )
            elif '_completed_' in event_key:
                step_num = int(event_key.split('_')[-1])
                user_ids = set(
                    events_qs.filter(event='onboarding_step_completed', properties__step=step_num)
                    .values_list('user_id', flat=True)
                    .distinct()
                )
            elif '_skipped_' in event_key:
                step_num = int(event_key.split('_')[-1])
                user_ids = set(
                    events_qs.filter(event='onboarding_step_skipped', properties__step=step_num)
                    .values_list('user_id', flat=True)
                    .distinct()
                )
            else:
                user_ids = set(events_qs.filter(event=event_key).values_list('user_id', flat=True).distinct())

            count = len(user_ids)
            pct = f"{(count / total_started * 100):.1f}%" if total_started > 0 else "0.0%"
            self.stdout.write(f"  {label:<28}: {count:>5} users ({pct})")

        self.stdout.write("\n------------------------------------------")
        self.stdout.write("             ACTIVATION METRIC            ")
        self.stdout.write(" (3+ expenses logged within 48h of signup)")
        self.stdout.write("------------------------------------------")

        # Cohort split: Users created with onboarding v2 vs older
        v2_states = OnboardingState.objects.all().select_related('user')
        if since_dt:
            v2_states = v2_states.filter(started_at__gte=since_dt)

        activated_v2 = 0
        total_v2 = v2_states.count()

        for st in v2_states:
            user = st.user
            window_end = user.date_joined + timedelta(hours=48)
            exp_count = Expense.objects.filter(
                user=user,
                created_at__lte=window_end,
                is_deleted=False,
            ).count()
            if exp_count >= 3:
                activated_v2 += 1

        v2_pct = f"{(activated_v2 / total_v2 * 100):.1f}%" if total_v2 > 0 else "N/A"
        self.stdout.write(f"Onboarding v2 Cohort: {activated_v2}/{total_v2} activated ({v2_pct})\n")
