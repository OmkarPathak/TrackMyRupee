from django.apps import AppConfig


class ExpensesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'expenses'

    def ready(self):
        from . import signals  # noqa: F401
        from .family import signals as family_signals  # noqa: F401
