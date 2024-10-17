from django.apps import AppConfig
from django.conf import settings
from django_celery_beat.models import IntervalSchedule, PeriodicTask


class L10nConfig(AppConfig):
    name = "kitsune.l10n"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        import kitsune.l10n.signals  # noqa

        value, unit_of_time = settings.SUMO_L10N_HEARTBEAT_PERIOD.split()
        interval = IntervalSchedule.objects.get_or_create(period=unit_of_time, every=int(value))[0]
        PeriodicTask.objects.get_or_create(
            interval=interval, name="SUMO L10N Heartbeat", task="kitsune.l10n.tasks.heartbeat"
        )
