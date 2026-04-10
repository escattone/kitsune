from datetime import timedelta
from unittest import mock

from celery.schedules import crontab
from django.test.utils import override_settings
from django.utils import timezone

from kitsune.sumo.tests import TestCase
from kitsune.sumo.watchdog import get_overdue_tasks, send_email_alert

WATCHDOG_SETTINGS = {
    "WATCHDOG_EMAIL_RECIPIENTS": ["test@example.com"],
    "WATCHDOG_GRACE_MULTIPLIER": 2.0,
    "WATCHDOG_ALERT_COOLDOWN_SECONDS": 86400,
    "WATCHDOG_EXCLUDED_TASKS": [],
}


@override_settings(**WATCHDOG_SETTINGS)
class TestGetOverdueTasks(TestCase):
    @override_settings(
        CELERY_BEAT_SCHEDULE={
            "my_task": {
                "task": "some.task",
                "schedule": crontab(minute="0"),  # hourly
            },
        }
    )
    @mock.patch("kitsune.sumo.watchdog.PeriodicTask")
    def test_detects_overdue_task(self, MockPeriodicTask):
        mock_task = mock.Mock()
        mock_task.last_run_at = timezone.now() - timedelta(hours=12)
        MockPeriodicTask.objects.get.return_value = mock_task

        overdue = get_overdue_tasks()
        self.assertEqual(len(overdue), 1)
        self.assertEqual(overdue[0][0], "my_task")

    @override_settings(
        CELERY_BEAT_SCHEDULE={
            "my_task": {
                "task": "some.task",
                "schedule": crontab(minute="0"),  # hourly
            },
        }
    )
    @mock.patch("kitsune.sumo.watchdog.PeriodicTask")
    def test_skips_recent_task(self, MockPeriodicTask):
        mock_task = mock.Mock()
        mock_task.last_run_at = timezone.now() - timedelta(minutes=30)
        MockPeriodicTask.objects.get.return_value = mock_task

        overdue = get_overdue_tasks()
        self.assertEqual(len(overdue), 0)

    @override_settings(
        CELERY_BEAT_SCHEDULE={
            "my_task": {
                "task": "some.task",
                "schedule": crontab(minute="0"),
            },
        }
    )
    @mock.patch("kitsune.sumo.watchdog.PeriodicTask")
    def test_skips_null_last_run_at(self, MockPeriodicTask):
        mock_task = mock.Mock()
        mock_task.last_run_at = None
        MockPeriodicTask.objects.get.return_value = mock_task

        overdue = get_overdue_tasks()
        self.assertEqual(len(overdue), 0)

    @override_settings(
        CELERY_BEAT_SCHEDULE={
            "my_task": {
                "task": "some.task",
                "schedule": crontab(minute="0"),
            },
        }
    )
    @mock.patch("kitsune.sumo.watchdog.PeriodicTask")
    def test_skips_task_not_in_db(self, MockPeriodicTask):
        MockPeriodicTask.DoesNotExist = Exception
        MockPeriodicTask.objects.get.side_effect = Exception

        overdue = get_overdue_tasks()
        self.assertEqual(len(overdue), 0)

    @override_settings(
        CELERY_BEAT_SCHEDULE={
            "excluded_task": {
                "task": "some.task",
                "schedule": crontab(minute="0"),
            },
        },
        WATCHDOG_EXCLUDED_TASKS=["excluded_task"],
    )
    @mock.patch("kitsune.sumo.watchdog.PeriodicTask")
    def test_skips_excluded_tasks(self, MockPeriodicTask):
        overdue = get_overdue_tasks()
        self.assertEqual(len(overdue), 0)
        MockPeriodicTask.objects.get.assert_not_called()

    @override_settings(
        CELERY_BEAT_SCHEDULE={
            "watchdog": {
                "task": "kitsune.sumo.tasks.watchdog",
                "schedule": crontab(minute="*/5"),
            },
        }
    )
    @mock.patch("kitsune.sumo.watchdog.PeriodicTask")
    def test_skips_itself(self, MockPeriodicTask):
        overdue = get_overdue_tasks()
        self.assertEqual(len(overdue), 0)
        MockPeriodicTask.objects.get.assert_not_called()

    @override_settings(CELERY_BEAT_SCHEDULE={})
    def test_empty_schedule_returns_empty(self):
        overdue = get_overdue_tasks()
        self.assertEqual(len(overdue), 0)


@override_settings(**WATCHDOG_SETTINGS)
class TestTryAlert(TestCase):
    @mock.patch("kitsune.sumo.watchdog.redis_client")
    def test_claims_when_no_prior_alert(self, mock_redis_client):
        from kitsune.sumo.watchdog import try_alert

        mock_redis = mock.Mock()
        mock_redis.set.return_value = True
        mock_redis_client.return_value = mock_redis

        self.assertTrue(try_alert("my_task"))
        mock_redis.set.assert_called_once_with("watchdog:alerted:my_task", "1", nx=True, ex=86400)

    @mock.patch("kitsune.sumo.watchdog.redis_client")
    def test_does_not_claim_within_cooldown(self, mock_redis_client):
        from kitsune.sumo.watchdog import try_alert

        mock_redis = mock.Mock()
        mock_redis.set.return_value = False
        mock_redis_client.return_value = mock_redis

        self.assertFalse(try_alert("my_task"))

    @mock.patch("kitsune.sumo.watchdog.redis_client")
    def test_returns_true_on_redis_failure(self, mock_redis_client):
        from kitsune.sumo.redis_utils import RedisError
        from kitsune.sumo.watchdog import try_alert

        mock_redis_client.side_effect = RedisError("connection refused")

        self.assertTrue(try_alert("my_task"))


@override_settings(**WATCHDOG_SETTINGS)
class TestSendEmailAlert(TestCase):
    @mock.patch("kitsune.sumo.watchdog.send_mail")
    def test_sends_email(self, mock_send_mail):
        now = timezone.now()
        overdue_tasks = [
            ("my_task", now - timedelta(hours=12), now - timedelta(hours=11)),
        ]

        send_email_alert(overdue_tasks)

        mock_send_mail.assert_called_once()
        args = mock_send_mail.call_args
        self.assertIn("1 task(s) overdue", args[0][0])
        self.assertIn("my_task", args[0][1])
        self.assertEqual(args[0][3], ["test@example.com"])

    @override_settings(WATCHDOG_EMAIL_RECIPIENTS=[])
    @mock.patch("kitsune.sumo.watchdog.send_mail")
    def test_skips_when_no_recipients(self, mock_send_mail):
        now = timezone.now()
        send_email_alert([("task", now - timedelta(hours=12), now - timedelta(hours=11))])
        mock_send_mail.assert_not_called()


@override_settings(**WATCHDOG_SETTINGS)
class TestWatchdog(TestCase):
    @override_settings(CELERY_BEAT_SCHEDULE={})
    @mock.patch("kitsune.sumo.tasks.send_email_alert")
    def test_noop_with_empty_schedule(self, mock_send_email):
        from kitsune.sumo.tasks import watchdog

        watchdog()
        mock_send_email.assert_not_called()

    @override_settings(
        CELERY_BEAT_SCHEDULE={
            "my_task": {
                "task": "some.task",
                "schedule": crontab(minute="0"),
            },
        }
    )
    @mock.patch("kitsune.sumo.tasks.try_alert", return_value=True)
    @mock.patch("kitsune.sumo.tasks.send_email_alert")
    @mock.patch("kitsune.sumo.watchdog.PeriodicTask")
    def test_sends_alert_for_overdue_task(self, MockPeriodicTask, mock_send_email, mock_try_alert):
        from kitsune.sumo.tasks import watchdog

        mock_task = mock.Mock()
        mock_task.last_run_at = timezone.now() - timedelta(hours=12)
        MockPeriodicTask.objects.get.return_value = mock_task

        watchdog()

        mock_send_email.assert_called_once()
        mock_try_alert.assert_called_once_with("my_task")

    @override_settings(
        CELERY_BEAT_SCHEDULE={
            "my_task": {
                "task": "some.task",
                "schedule": crontab(minute="0"),
            },
        }
    )
    @mock.patch("kitsune.sumo.tasks.try_alert", return_value=False)
    @mock.patch("kitsune.sumo.tasks.send_email_alert")
    @mock.patch("kitsune.sumo.watchdog.PeriodicTask")
    def test_suppresses_alert_within_cooldown(
        self, MockPeriodicTask, mock_send_email, mock_try_alert
    ):
        from kitsune.sumo.tasks import watchdog

        mock_task = mock.Mock()
        mock_task.last_run_at = timezone.now() - timedelta(hours=12)
        MockPeriodicTask.objects.get.return_value = mock_task

        watchdog()

        mock_send_email.assert_not_called()
