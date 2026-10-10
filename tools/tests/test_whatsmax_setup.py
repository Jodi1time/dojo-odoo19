import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_whatsmax_setup as setup
from private_env import load_private_env
import sync_whatsmax_hub as sync


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "runtime.env"
        self.config = {
            "APP_URL": "https://messaging.dojo.org", "NEXTAUTH_URL": "https://messaging.dojo.org",
            "APP_ENV": "production", "APP_DEMO_MODE": "false", "SEED_DEMO_DATA": "false",
            "AUTH_SECRET": "a" * 48, "APP_ENCRYPTION_KEY": "a1" * 32,
            "DATABASE_URL": "mysql://dojo:encoded-secret@mysql:3306/whatsmax",
            "REDIS_URL": "redis://redis:6379", "MAIL_TRANSPORT": "log", "STORAGE_DRIVER": "local",
        }

    def env_file(self, text):
        self.path.write_text(text)
        self.path.chmod(0o600)
        return self.path

    def test_valid_settings_never_claim_live_services_or_delivery(self):
        result = setup.config_report(self.config)
        self.assertEqual(result["status"], "configuration_checks_passed")
        self.assertFalse(result["liveMessagingVerified"])
        self.assertEqual(result["messagesSent"], 0)
        checks = {c["check"]: c["status"] for c in result["checks"]}
        self.assertEqual(checks["runtime_services"], "pending")
        self.assertEqual(checks["mail_transport"], "warning")

    def test_fresh_install_mistakes_are_blocked_without_echoing_values(self):
        errors = {"DATABASE_URL": "postgresql://dojo:private-value@postgres/odoo",
                  "APP_URL": "https://replace-with-whatsmax-host", "APP_ENV": "development",
                  "APP_DEMO_MODE": "true", "SEED_DEMO_DATA": "true", "AUTH_SECRET": "replace-me",
                  "APP_ENCRYPTION_KEY": "0" * 64, "REDIS_URL": "not-a-url", "NEXTAUTH_URL": "http://localhost:3000"}
        for key, value in errors.items():
            with self.subTest(key=key):
                result = setup.config_report({**self.config, key: value})
                self.assertEqual(result["status"], "blocked")
                self.assertNotIn(value, json.dumps(result))

    def test_dotenv_preserves_literal_sanctum_token_and_does_not_expand(self):
        path = self.env_file('# comment\nTOKEN="12|opaque#secret"\nURL=redis://:a%40b@redis:6379\nEMPTY=\n')
        values = load_private_env(path)
        self.assertEqual(values["TOKEN"], "12|opaque#secret")
        self.assertEqual(values["URL"], "redis://:a%40b@redis:6379")
        self.assertEqual(values["EMPTY"], "")
        for text in ('TOKEN=$(echo bad)', 'TOKEN=${HOME}', 'TOKEN=`id`', 'TOKEN=a\nTOKEN=b', 'export TOKEN=a', 'TOKEN="unclosed'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                load_private_env(self.env_file(text))

    def test_dotenv_rejects_public_permissions_symlinks_and_fifos(self):
        self.env_file("TOKEN=private")
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):
            load_private_env(self.path)
        self.path.chmod(0o600)
        link = self.path.with_suffix(".link")
        link.symlink_to(self.path)
        with self.assertRaises(OSError):
            load_private_env(link)
        fifo = self.path.with_suffix(".fifo")
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(ValueError):
            load_private_env(fifo)

    def test_cli_reports_bad_private_file_without_disclosing_contents(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = setup.main(["config", "--env-file", str(self.env_file("BROKEN private-key-do-not-print"))])
        self.assertEqual(code, 1)
        self.assertNotIn("private-key-do-not-print", output.getvalue())

    def test_sync_reads_private_file_without_mutating_process_environment(self):
        state = str(self.path.with_suffix(".sqlite"))
        values = {"DOJANG_HUB_PROVIDER_INGRESS_VERIFIED": "true", "DOJANG_HUB_SYNC_STATE": state,
                  "DOJANG_HUB_WHATSMAX_ORIGIN": "https://messaging.dojo.org", "DOJANG_HUB_ODOO_ORIGIN": "https://odoo.dojo.org",
                  "DOJANG_HUB_SITE_ID": "1", "DOJANG_HUB_WORKSPACE_ID": "11", "DOJANG_HUB_WHATSMAX_TOKEN": "private-token",
                  "DOJANG_HUB_WEBHOOK_SECRET": "private-secret", "DOJANG_HUB_SYNC_SINCE": "2026-10-10T00:00:00Z"}
        self.env_file("\n".join(k + "=" + v for k, v in values.items()))
        output = io.StringIO()
        with patch.object(sync, "Sync") as task, patch.dict(os.environ, {"DOJANG_HUB_WORKSPACE_ID": "999"}), contextlib.redirect_stdout(output):
            task.return_value.run.return_value = {"messagesForwarded": 0}
            sync.main(["--env-file", str(self.path)])
            self.assertEqual(task.call_args.args[3], "11")
            self.assertEqual(os.environ["DOJANG_HUB_WORKSPACE_ID"], "999")
        self.assertNotIn("private-token", output.getvalue())
        self.assertEqual(os.stat(state).st_mode & 0o777, 0o600)

    def test_env_file_does_not_bypass_provider_authentication_gate(self):
        self.env_file("DOJANG_HUB_PROVIDER_INGRESS_VERIFIED=false")
        with patch.object(sync, "Sync") as task, self.assertRaises(ValueError):
            sync.main(["--env-file", str(self.path)])
        task.assert_not_called()


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.values = {"DOJANG_HUB_WHATSMAX_ORIGIN": "https://messaging.dojo.org",
                       "DOJANG_HUB_WORKSPACE_ID": "11", "DOJANG_HUB_WHATSMAX_TOKEN": "1|" + "t" * 48}
        self.profile = {"workspace_id": 11, "demo_mode": False, "email": "private-parent@example.org"}
        self.conversations = {"data": [{"id": 7, "contact_id": 42, "workspace_id": 11}], "links": {"next": None}}
        self.messages = {"data": [{"id": 12, "conversation_id": 7, "body": "private parent message",
                                   "created_at": "2026-10-10T00:00:00Z"}], "links": {"next": None}}
        self.http = Mock()
        self.http.get.side_effect = self.read

    def read(self, url, **kwargs):
        data = self.profile if url.endswith("auth/me") else self.messages if url.endswith("/messages") else self.conversations
        return Mock(status_code=200, content=json.dumps(data).encode(), json=lambda: data)

    def test_reads_actual_vendor_shape_and_never_sends_or_discloses_pii(self):
        result = setup.connection_report(self.values, self.http)
        self.assertEqual(result["status"], "read_connection_verified")
        self.assertEqual(self.http.get.call_count, 3)
        self.http.post.assert_not_called()
        for call in self.http.get.call_args_list:
            self.assertTrue(call.args[0].startswith(self.values["DOJANG_HUB_WHATSMAX_ORIGIN"] + "/api/v1/"))
            self.assertFalse(call.kwargs["allow_redirects"])
        for private in ("private-parent", "private parent message", self.values["DOJANG_HUB_WHATSMAX_TOKEN"]):
            self.assertNotIn(private, json.dumps(result))
        self.assertFalse(result["liveMessagingVerified"])
        self.assertEqual(result["checks"][-1]["status"], "pending")

    def test_wrong_workspace_or_demo_stops_before_reading_conversations(self):
        for profile in ({"workspace_id": 12, "demo_mode": False}, {"workspace_id": 11, "demo_mode": "false"}):
            self.profile = profile
            self.http.reset_mock()
            result = setup.connection_report(self.values, self.http)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(self.http.get.call_count, 1)

    def test_empty_install_does_not_invent_a_message_api_or_delivery_test(self):
        self.conversations["data"] = []
        result = setup.connection_report(self.values, self.http)
        self.assertEqual(result["status"], "read_connection_verified")
        self.assertEqual(self.http.get.call_count, 2)
        self.assertEqual(next(c for c in result["checks"] if c["check"] == "message_api")["status"], "pending")

    def test_cross_scope_rows_and_changed_pagination_origin_fail_closed(self):
        for mutate in (lambda: self.conversations["data"][0].update(workspace_id=12),
                       lambda: self.conversations["links"].update(next="https://attacker.test/api/v1/conversations")):
            mutate()
            result = setup.connection_report(self.values, self.http)
            self.assertEqual(result["status"], "blocked")
            self.http.post.assert_not_called()
            self.conversations["data"][0]["workspace_id"] = 11
        self.assertFalse(any("attacker" in call.args[0] for call in self.http.get.call_args_list))

    def test_old_starter_array_shape_and_cross_thread_messages_are_rejected(self):
        self.conversations = {"data": [{"id": "7"}]}
        self.assertEqual(setup.connection_report(self.values, self.http)["status"], "blocked")
        self.conversations = {"data": [{"id": 7, "contact_id": 42, "workspace_id": 11}], "links": {"next": None}}
        self.messages["data"][0]["conversation_id"] = 8
        self.assertEqual(setup.connection_report(self.values, self.http)["status"], "blocked")

    def test_provider_errors_and_missing_token_do_not_leak_response_or_make_writes(self):
        self.http.get.side_effect = None
        self.http.get.return_value = Mock(status_code=403, content=b"sensitive upstream error")
        result = setup.connection_report(self.values, self.http)
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("sensitive upstream", json.dumps(result))
        self.http.reset_mock()
        result = setup.connection_report({**self.values, "DOJANG_HUB_WHATSMAX_TOKEN": ""}, self.http)
        self.assertEqual(result["status"], "blocked")
        self.http.get.assert_not_called()
        self.http.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
