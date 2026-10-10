import importlib.util
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location("sync_hub", Path(__file__).parents[1] / "sync_whatsmax_hub.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.http = Mock()
        self.http.post.return_value = self.response({"accepted": True})
        self.sync = module.Sync("https://whatsmax.example.test", "https://odoo.example.test", "1", "11", "t" * 48, "s" * 48,
            "2026-10-09T00:00:00Z", self.db, self.http)
        self.conversation = {"id": 7, "contact_id": 42, "workspace_id": 11, "channel": "sms"}
        self.message = {"id": 10, "conversation_id": 7, "direction": "in", "channel": "sms", "type": "text", "body": "Please arrange follow-up.", "created_at": "2026-10-10T00:00:00Z"}
        self.http.get.side_effect = self.read

    @staticmethod
    def response(value):
        return Mock(status_code=200, content=json.dumps(value).encode(), json=lambda: value)

    def read(self, url, **kw):
        if url.endswith("/auth/me"):
            return self.response({"workspace_id": 11, "demo_mode": False})
        if url.endswith("/conversations"):
            return self.response({"data": [self.conversation], "links": {"next": None}})
        return self.response({"data": [self.message], "links": {"next": None}})

    def test_real_api_shapes_map_to_signed_event_and_durable_dedup(self):
        self.assertEqual(self.sync.run()["messagesForwarded"], 1)
        self.assertEqual(self.sync.run()["duplicates"], 1)
        self.assertEqual(self.http.post.call_count, 1)
        sent = self.http.post.call_args.kwargs
        self.assertEqual(json.loads(sent["data"])["contactRef"], "42")
        self.assertIn("X-Dojang-Signature", sent["headers"])
        self.assertFalse(sent["allow_redirects"])
        self.assertNotIn("Please", str(self.db.execute("SELECT * FROM forwarded").fetchall()))

    def test_failed_forwarding_never_advances_checkpoint(self):
        self.http.post.return_value = Mock(status_code=503)
        with self.assertRaises(ValueError):
            self.sync.run()
        self.assertEqual(self.db.execute("SELECT count(*) FROM forwarded").fetchone()[0], 0)

    def test_cross_workspace_message_never_forwarded(self):
        self.conversation["workspace_id"] = 12
        with self.assertRaises(ValueError):
            self.sync.run()
        self.http.post.assert_not_called()

    def test_pagination_cannot_leak_bearer_to_another_host(self):
        self.http.get.return_value = self.response({"data": [], "links": {"next": "https://attacker.test/api/v1/conversations"}})
        self.http.get.side_effect = None
        with self.assertRaises(ValueError):
            list(self.sync._pages("/api/v1/conversations"))
        self.assertEqual(self.http.get.call_count, 1)

    def test_status_mapping_preserves_provider_receipt_and_never_claims_sent_is_delivered(self):
        self.message.update(direction="out", status="sent", provider_message_id="provider-100")
        self.assertEqual(self.sync.run()["statusesForwarded"], 0)
        self.message["status"] = "delivered"
        self.assertEqual(self.sync.run()["statusesForwarded"], 1)
        self.assertEqual(json.loads(self.http.post.call_args.kwargs["data"])["providerRef"], "provider-100")

    def test_historical_and_unsupported_messages_are_not_silently_truncated(self):
        self.message["created_at"] = "2026-10-01T00:00:00Z"
        self.assertEqual(self.sync.run()["messagesForwarded"], 0)
        self.message.update(created_at="2026-10-10T00:00:00Z", body="x" * 1600)
        self.assertEqual(self.sync.run()["unsupportedMessages"], 1)


if __name__ == "__main__":
    unittest.main()
