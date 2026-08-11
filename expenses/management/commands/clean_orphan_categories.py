from django.core.exceptions import FieldDoesNotExist
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Exists, OuterRef, Q

from expenses.models import (
    Category,
    Expense,
    GoalContribution,
    Income,
    Loan,
    LoanRepayment,
    SavingsGoal,
)


class Command(BaseCommand):
    help = (
        "Delete categories that are not linked to Income/Expense/Loan/Goal records. "
        "Use --dry-run to preview."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--user-id",
            type=int,
            help="Only clean categories for a specific user ID.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show which categories would be deleted without deleting them.",
        )

    @staticmethod
    def _build_exists_annotation(model, field_name):
        """Build an Exists() annotation only when model.field_name is a FK to Category."""
        try:
            field = model._meta.get_field(field_name)
        except FieldDoesNotExist:
            return None

        remote_model = getattr(getattr(field, "remote_field", None), "model", None)
        if remote_model is not Category:
            return None

        return Exists(model.objects.filter(**{f"{field_name}_id": OuterRef("pk")}))

    def handle(self, *args, **options):
        user_id = options.get("user_id")
        dry_run = options.get("dry_run", False)

        base_qs = Category.objects.all().order_by("user_id", "name")
        if user_id:
            base_qs = base_qs.filter(user_id=user_id)

        annotations = {
            "has_expense_link": Exists(Expense.objects.filter(category_fk_id=OuterRef("pk"))),
            "has_expense_name_link": Exists(
                Expense.objects.filter(
                    user_id=OuterRef("user_id"),
                    category__isnull=False,
                )
                .exclude(category="")
                .filter(category__iexact=OuterRef("name"))
            ),
            "has_income_name_link": Exists(
                Income.objects.filter(
                    user_id=OuterRef("user_id"),
                    source__isnull=False,
                )
                .exclude(source="")
                .filter(source__iexact=OuterRef("name"))
            ),
            # Loan/Goal are name-based in the current schema (no category FK fields).
            "has_loan_name_link": Exists(
                Loan.objects.filter(user_id=OuterRef("user_id")).filter(name__iexact=OuterRef("name"))
            ),
            "has_goal_name_link": Exists(
                SavingsGoal.objects.filter(user_id=OuterRef("user_id")).filter(name__iexact=OuterRef("name"))
            ),
        }

        # Loan/Goal models do not currently have category FK fields in this codebase,
        # but we keep this dynamic so the command remains valid if those fields exist.
        optional_links = [
            (Loan, "category_fk", "has_loan_link"),
            (LoanRepayment, "category_fk", "has_loan_repayment_link"),
            (SavingsGoal, "category_fk", "has_goal_link"),
            (GoalContribution, "category_fk", "has_goal_contribution_link"),
        ]
        for model, field_name, alias in optional_links:
            exists_expr = self._build_exists_annotation(model, field_name)
            if exists_expr is not None:
                annotations[alias] = exists_expr

        q_keep = (
            Q(has_expense_link=True)
            | Q(has_expense_name_link=True)
            | Q(has_income_name_link=True)
            | Q(has_loan_name_link=True)
            | Q(has_goal_name_link=True)
        )
        for alias in annotations.keys():
            if alias in {
                "has_expense_link",
                "has_expense_name_link",
                "has_income_name_link",
                "has_loan_name_link",
                "has_goal_name_link",
            }:
                continue
            q_keep |= Q(**{alias: True})

        annotated_qs = base_qs.annotate(**annotations)
        to_delete_qs = annotated_qs.filter(~q_keep)

        total_categories = base_qs.count()
        keep_count = annotated_qs.filter(q_keep).count()
        delete_count = to_delete_qs.count()

        self.stdout.write(
            self.style.SUCCESS(
                f"Scanned {total_categories} categories: keep={keep_count}, delete={delete_count}."
            )
        )

        if delete_count == 0:
            self.stdout.write(self.style.SUCCESS("No orphan categories found."))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run enabled. No categories were deleted."))
            for category in to_delete_qs.values("id", "user_id", "name")[:50]:
                self.stdout.write(
                    f"- id={category['id']} user_id={category['user_id']} name={category['name']}"
                )
            if delete_count > 50:
                self.stdout.write(f"... and {delete_count - 50} more")
            return

        with transaction.atomic():
            deleted_total, _ = to_delete_qs.delete()

        self.stdout.write(self.style.SUCCESS(f"Deleted {delete_count} categories."))
        self.stdout.write(f"Cascade delete summary rows removed: {deleted_total}")