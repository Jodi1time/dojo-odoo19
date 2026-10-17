import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock
from urllib.error import HTTPError, URLError

spec = importlib.util.spec_from_file_location("openai_conversation", Path(__file__).parents[2] /
    "addons/ai_assistant/models/openai_conversation.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class OpenAIConversationTests(unittest.TestCase):
    def setUp(self):
        self.key = "synthetic-test-secret-" * 3
        self.get = {"openai.api_key": self.key}.get
        self.opener = MagicMock()
        self.response = self.opener.open.return_value.__enter__.return_value
        self.response.status = 200
        self.result = {"choices": [{"finish_reason": "stop", "message": {"content": "Draft for review"}}]}

    def call(self):
        self.response.read.return_value = json.dumps(self.result).encode()
        return module.complete((self.key, "gpt-4o-mini"), "Synthetic parent report", "Draft only", self.opener)

    def test_runtime_secret_and_model_override_legacy_settings(self):
        self.assertEqual(module.configuration(self.get, {"DOJANG_OPENAI_API_KEY": "new-synthetic-secret-123",
            "DOJANG_OPENAI_CHAT_MODEL": "approved-model"}), ("new-synthetic-secret-123", "approved-model"))
        self.assertEqual(module.configuration(self.get, {}), (self.key, "gpt-4o-mini"))
        self.assertEqual(module.configuration({"elevenlabs_connector.openai_api_key": self.key,
            "openai.chat_model": "approved-model"}.get, {}), (self.key, "approved-model"))

    def test_explicit_empty_key_does_not_reactivate_old_database_key(self):
        with self.assertRaises(module.ProviderUnavailable):
            module.configuration(self.get, {"DOJANG_OPENAI_API_KEY": ""})

    def test_bad_secret_and_model_rejected_without_echo(self):
        for overrides in [{"DOJANG_OPENAI_API_KEY": self.key + "\n"}, {"DOJANG_OPENAI_CHAT_MODEL": "https://secret.test"}]:
            with self.assertRaises(module.ProviderUnavailable) as error:
                module.configuration(self.get, overrides)
            self.assertNotIn(self.key, str(error.exception))

    def test_request_has_no_tools_redirects_or_stored_completion(self):
        self.assertEqual(self.call(), "Draft for review")
        req = self.opener.open.call_args.args[0]
        self.assertEqual(req.full_url, "https://api.openai.com/v1/chat/completions")
        payload = json.loads(req.data)
        self.assertEqual(payload["max_completion_tokens"], 1000)
        self.assertFalse(payload["store"])
        self.assertNotIn("tools", payload)
        self.assertEqual(payload["messages"][1]["content"], "Synthetic parent report")
        self.assertEqual(self.opener.open.call_args.kwargs["timeout"], 30)
        self.assertIsNone(module.NoRedirect().redirect_request(req, None, 302, "", {}, "https://other.test"))

    def test_failures_do_not_echo_provider_body_key_or_prompt_or_retry(self):
        for failure in [HTTPError("https://api.openai.com", 429, self.key, {}, None), URLError(self.key), TimeoutError(self.key)]:
            self.opener.reset_mock()
            self.opener.open.side_effect = failure
            with self.assertRaises(module.ProviderUnavailable) as error:
                self.call()
            self.assertNotIn(self.key, str(error.exception))
            self.assertEqual(self.opener.open.call_count, 1)

    def test_incomplete_refused_and_tool_outputs_are_not_drafts(self):
        for choice in [
            {"finish_reason": "length", "message": {"content": "partial"}},
            {"finish_reason": "stop", "message": {"content": "text", "refusal": "refused"}},
            {"finish_reason": "stop", "message": {"content": "text", "tool_calls": [{}]}},
            {"finish_reason": "stop", "message": {"content": None}},
            {"finish_reason": "stop", "message": {"content": "x" * 32001}},
        ]:
            self.result = {"choices": [choice]}
            with self.assertRaises(module.ProviderUnavailable):
                self.call()

    def test_malformed_or_oversized_provider_response_is_bounded(self):
        for raw in [b"not json", b"[]", b"{}", b"x" * 131073]:
            self.response.read.return_value = raw
            with self.assertRaises(module.ProviderUnavailable):
                module.complete((self.key, "gpt-4o-mini"), "Synthetic", "Draft only", self.opener)
            self.response.read.assert_called_with(131073)
            self.opener.open.return_value.__exit__.assert_called()


if __name__ == "__main__":
    unittest.main()
