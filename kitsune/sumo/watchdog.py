import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from django.utils.timesince import timesince
from django_celery_beat.models import PeriodicTask
from redis import ConnectionError as RedisConnectionError

from kitsune.sumo.redis_utils import RedisError, redis_client

log = logging.getLogger("k.watchdog")


def get_overdue_tasks():
    """Return a list of (task_name, last_run_at, expected_next_run) for overdue tasks."""
    beat_schedule = settings.CELERY_BEAT_SCHEDULE
    if not beat_schedule:
        return []

    now = timezone.now()
    excluded = set(settings.WATCHDOG_EXCLUDED_TASKS)
    overdue = []

    for task_name, task_config in beat_schedule.items():
        if task_name == "watchdog":
            continue
        if task_name in excluded:
            continue

        schedule = task_config["schedule"]

        try:
            periodic_task = PeriodicTask.objects.get(name=task_name)
        except PeriodicTask.DoesNotExist:
            continue

        last_run_at = periodic_task.last_run_at
        if last_run_at is None:
            continue

        expected_gap = schedule.remaining_estimate(last_run_at)
        deadline = last_run_at + (expected_gap * settings.WATCHDOG_GRACE_MULTIPLIER)

        if now > deadline:
            expected_next_run = last_run_at + expected_gap
            overdue.append((task_name, last_run_at, expected_next_run))

    return overdue


def try_alert(task_name):
    """Atomically check and claim an alert slot for this task.

    Returns True if we should send an alert (no recent alert exists),
    False if an alert was already sent within the cooldown period.
    """
    try:
        r = redis_client("default")
        key = f"watchdog:alerted:{task_name}"
        return r.set(key, "1", nx=True, ex=settings.WATCHDOG_ALERT_COOLDOWN_SECONDS)
    except (RedisError, RedisConnectionError):
        log.exception("Redis unavailable for alert suppression")
        return True


def send_email_alert(overdue_tasks):
    """Send an email alert for overdue tasks."""
    recipients = settings.WATCHDOG_EMAIL_RECIPIENTS
    if not recipients:
        return

    subject = f"Celery Beat Watchdog: {len(overdue_tasks)} task(s) overdue"
    lines = []
    for task_name, last_run_at, expected_next_run in overdue_tasks:
        overdue_by = timesince(expected_next_run)
        lines.append(
            f"- {task_name}\n"
            f"  Last run: {last_run_at:%Y-%m-%d %H:%M:%S %Z}\n"
            f"  Expected next run: {expected_next_run:%Y-%m-%d %H:%M:%S %Z}\n"
            f"  Overdue by: {overdue_by}"
        )

    message = "\n\n".join(lines)
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, recipients)
