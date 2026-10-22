"""Say, rule by rule, why a person does or does not see an insight. Read-only: it evaluates the rules and reads the
stored rows but writes nothing, and it prints rule ids and statuses only (never an amount, a name or a category)."""
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone, translation

from expenses.insights import channel_enabled, engine, engine_mode, prefs, registry
from expenses.insights.context import InsightContext
from expenses.insights.gating import plan_of, rule_allowed
from expenses.insights.types import Module, Surface
from expenses.models import InsightState


class Command(BaseCommand):
    help = 'Explain which insights a user has, and why the others are quiet (read-only, ids and counts only).'

    def add_arguments(self, parser):
        parser.add_argument('--user-id', type=int)
        parser.add_argument('--username')

    def handle(self, *args, **options):
        users = User.objects.select_related('profile')
        if options['user_id']:
            user = users.filter(pk=options['user_id']).first()
        elif options['username']:
            user = users.filter(username=options['username']).first()
        else:
            user = users.order_by(F('last_login').desc(nulls_last=True)).first()
        if user is None:
            raise CommandError('no such user')
        profile = user.profile
        current = prefs.get(profile)
        plan = plan_of(profile)
        today = timezone.localdate()
        out = self.stdout.write
        out(f'user={user.pk} engine_mode={engine_mode(user)} plan={plan.name} enabled={current["enabled"]} '
            f'notify={current["notify"]} modules_off={current["modules_off"]} muted={sorted(current["muted"])} '
            f'stale={profile.insights_stale} generated_at={profile.insights_generated_at}')
        out(f'channels bell={channel_enabled("bell")} push={channel_enabled("push")}')
        out(f'the next dashboard or strip visit will re-run the quick rules: {engine.needs_refresh(profile)}')

        out('\nrules (evaluated now, nothing stored):')
        with translation.override(profile.language or 'en'):
            ctx = InsightContext(user, today=today, profile=profile)
            for rule in registry.all_rules():
                if rule.mode == 'inline':
                    status = 'inline: worked out on the Analytics page, never stored'
                elif not prefs.module_on(current, rule.module):
                    status = 'module switched off'
                elif not rule_allowed(rule.min_plan, plan):
                    status = f'needs {rule.min_plan.name} (a preview at most)'
                elif rule.min_history and ctx.history_cycles() < rule.min_history:
                    status = f'not enough history ({ctx.history_cycles()} of {rule.min_history} cycles)'
                else:
                    try:
                        with ctx.declared(rule.needs):
                            found = rule.evaluate(ctx)
                        status = f'speaks ({len(found)}), surfaces={sorted({s.value for i in found for s in i.surfaces})}' \
                            if found else 'nothing to say'
                    except Exception as exc:        # noqa: BLE001
                        status = f'FAILED {type(exc).__name__}'
                out(f'  {rule.id:36} {rule.cost:5} {status}')

        rows = list(InsightState.objects.filter(user=user))
        out(f'\nstored rows: {len(rows)}')
        now = timezone.now()
        for row in sorted(rows, key=lambda r: (r.rule_id, r.dedupe_key)):
            state = ('dismissed' if row.dismissed_at else 'acted' if row.acted_at else
                     'snoozed' if row.snoozed_until and row.snoozed_until > now else
                     'expired' if row.valid_until < today else 'visible')
            out(f'  {row.rule_id:36} {state:9} locked={row.locked} shown={row.times_shown}')

        out('\nwhat each place would show now:')
        for label, surface, modules, skip in (
            ('dashboard', Surface.DASHBOARD, None, ()),
            ('budgets strip', Surface.MODULE, {Module.BUDGETS}, ('budgets.limit_passed', 'budgets.limit_nearing', 'budgets.room_to_share')),
            ('expenses strip', Surface.MODULE, {Module.EXPENSES, Module.CASHFLOW}, ()),
        ):
            shown = engine.candidates(user, surface, modules=modules, profile=profile, skip_rules=skip)
            out(f'  {label:15} {[i.rule_id for i, _row in shown]}')
