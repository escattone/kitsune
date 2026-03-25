import base64
import hashlib
import hmac
import json

from django.test import override_settings

from kitsune.customercare.models import SupportTicket
from kitsune.customercare.tasks import process_zendesk_update
from kitsune.products.tests import ProductFactory
from kitsune.sumo.tests import TestCase

WEBHOOK_API_KEY = "test-webhook-api-key"
WEBHOOK_SIGNING_SECRET = "test-webhook-secret"
WEBHOOK_URL = "/customercare/zendesk/updates"


def _sign_payload(body, timestamp="1234567890", secret=WEBHOOK_SIGNING_SECRET):
    """Generate a valid HMAC-SHA256 signature for the given payload."""
    message = timestamp.encode("utf-8") + body
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


@override_settings(
    ZENDESK_WEBHOOK_API_KEY=WEBHOOK_API_KEY,
    ZENDESK_WEBHOOK_SIGNING_SECRET=WEBHOOK_SIGNING_SECRET,
)
class ZendeskWebhookViewTests(TestCase):
    """Tests for ZendeskWebhookView authentication and request handling."""

    def _post(self, payload, signature=None, timestamp="1234567890", api_key=WEBHOOK_API_KEY):
        body = json.dumps(payload).encode("utf-8")
        if signature is None:
            signature = _sign_payload(body, timestamp)
        headers = {
            "HTTP_X_ZENDESK_WEBHOOK_SIGNATURE": signature,
            "HTTP_X_ZENDESK_WEBHOOK_SIGNATURE_TIMESTAMP": timestamp,
        }
        if api_key is not None:
            headers["HTTP_ZENDESK_WEBHOOK_API_KEY"] = api_key
        return self.client.post(
            WEBHOOK_URL,
            data=body,
            content_type="application/json",
            **headers,
        )

    def test_valid_request_returns_200(self):
        response = self._post({"ticket_id": "123", "status": "open"})
        self.assertEqual(response.status_code, 200)

    def test_missing_api_key_returns_403(self):
        response = self._post({"ticket_id": "123"}, api_key=None)
        self.assertEqual(response.status_code, 403)

    def test_invalid_api_key_returns_403(self):
        response = self._post({"ticket_id": "123"}, api_key="wrong-key")
        self.assertEqual(response.status_code, 403)

    def test_missing_signature_returns_403(self):
        body = json.dumps({"ticket_id": "123"}).encode("utf-8")
        response = self.client.post(
            WEBHOOK_URL,
            data=body,
            content_type="application/json",
            HTTP_ZENDESK_WEBHOOK_API_KEY=WEBHOOK_API_KEY,
        )
        self.assertEqual(response.status_code, 403)

    def test_missing_timestamp_returns_403(self):
        body = json.dumps({"ticket_id": "123"}).encode("utf-8")
        signature = _sign_payload(body)
        response = self.client.post(
            WEBHOOK_URL,
            data=body,
            content_type="application/json",
            HTTP_ZENDESK_WEBHOOK_API_KEY=WEBHOOK_API_KEY,
            HTTP_X_ZENDESK_WEBHOOK_SIGNATURE=signature,
        )
        self.assertEqual(response.status_code, 403)

    def test_invalid_signature_returns_403(self):
        response = self._post(
            {"ticket_id": "123"},
            signature=base64.b64encode(b"invalid").decode("utf-8"),
        )
        self.assertEqual(response.status_code, 403)

    def test_invalid_json_returns_400(self):
        body = b"not json"
        signature = _sign_payload(body)
        response = self.client.post(
            WEBHOOK_URL,
            data=body,
            content_type="application/json",
            HTTP_ZENDESK_WEBHOOK_API_KEY=WEBHOOK_API_KEY,
            HTTP_X_ZENDESK_WEBHOOK_SIGNATURE=signature,
            HTTP_X_ZENDESK_WEBHOOK_SIGNATURE_TIMESTAMP="1234567890",
        )
        self.assertEqual(response.status_code, 400)

    def test_empty_payload_returns_400(self):
        response = self._post({})
        self.assertEqual(response.status_code, 400)

    def test_get_method_not_allowed(self):
        response = self.client.get(WEBHOOK_URL)
        self.assertEqual(response.status_code, 405)


class ProcessZendeskUpdateTests(TestCase):
    """Tests for process_zendesk_update Celery task."""

    def setUp(self):
        self.product = ProductFactory(slug="firefox", title="Firefox")
        self.ticket = SupportTicket.objects.create(
            subject="Help",
            description="Need help",
            category="general",
            email="user@example.com",
            product=self.product,
            submission_status=SupportTicket.STATUS_SENT,
            zendesk_ticket_id="12345",
        )

    def test_updates_status(self):
        process_zendesk_update({"ticket_id": "12345", "status": "solved"})
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.zd_status, "solved")
        self.assertIsNotNone(self.ticket.last_synced_at)

    def test_updates_timestamp(self):
        process_zendesk_update(
            {
                "ticket_id": "12345",
                "updated_at": "2026-03-25T10:30:00Z",
            }
        )
        self.ticket.refresh_from_db()
        self.assertEqual(str(self.ticket.zd_updated_at), "2026-03-25 10:30:00+00:00")

    def test_appends_comment(self):
        comment = {"body": "Agent reply", "author_id": "99", "public": True}
        process_zendesk_update({"ticket_id": "12345", "latest_comment": comment})
        self.ticket.refresh_from_db()
        self.assertEqual(len(self.ticket.comments), 1)
        self.assertEqual(self.ticket.comments[0]["body"], "Agent reply")

    def test_appends_multiple_comments(self):
        """Successive webhook calls append to the comments list."""
        comment1 = {"body": "First reply", "author_id": "99", "public": True}
        comment2 = {"body": "Second reply", "author_id": "99", "public": True}
        process_zendesk_update({"ticket_id": "12345", "latest_comment": comment1})
        process_zendesk_update({"ticket_id": "12345", "latest_comment": comment2})
        self.ticket.refresh_from_db()
        self.assertEqual(len(self.ticket.comments), 2)

    def test_updates_tags(self):
        process_zendesk_update(
            {
                "ticket_id": "12345",
                "tags": ["escalated", "vpn"],
            }
        )
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.internal_zd_tags, ["escalated", "vpn"])

    def test_partial_payload_only_updates_present_fields(self):
        """A payload with only status should not touch comments or tags."""
        process_zendesk_update({"ticket_id": "12345", "status": "pending"})
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.zd_status, "pending")
        self.assertEqual(self.ticket.comments, [])
        self.assertEqual(self.ticket.internal_zd_tags, [])

    def test_no_matching_ticket_is_noop(self):
        """Webhook for a ticket not in our DB should not raise."""
        process_zendesk_update({"ticket_id": "99999", "status": "open"})

    def test_missing_ticket_id_is_noop(self):
        """Webhook payload without ticket_id should not raise."""
        process_zendesk_update({"status": "open"})
