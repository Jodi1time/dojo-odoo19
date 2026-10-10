"""Bounded, text-only OpenAI transport. No Odoo permissions or action execution."""
import json
import os
import re
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ProviderUnavailable(ValueError):
    """Safe to show to an operator; never includes provider bodies or secrets."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def configuration(get_parameter, environ=None):
    env = os.environ if environ is None else environ
    # An explicitly empty runtime key disables fallback to older database keys.
    key = env.get("DOJANG_OPENAI_API_KEY")
    if key is None:
        key = get_parameter("openai.api_key") or get_parameter("elevenlabs_connector.openai_api_key")
    model = env.get("DOJANG_OPENAI_CHAT_MODEL")
    if model is None:
        model = get_parameter("openai.chat_model") or "gpt-4o-mini"
    if not isinstance(key, str) or not 16 <= len(key) <= 8192 or any(not 33 <= ord(c) <= 126 for c in key):
        raise ProviderUnavailable("OpenAI credential is missing or invalid.")
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", model):
        raise ProviderUnavailable("OpenAI chat model is missing or invalid.")
    return key, model


def complete(config, text, system_prompt, opener=None):
    key, model = config
    payload = {"model": model, "messages": [
        {"role": "system", "content": system_prompt}, {"role": "user", "content": text}],
        "max_completion_tokens": 1000, "store": False}
    request = Request("https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json; charset=utf-8"})
    try:
        # No redirect or automatic retry. Read a bounded body, even on bad output.
        with (opener or build_opener(NoRedirect())).open(request, timeout=30) as response:
            if response.status != 200:
                raise ProviderUnavailable("OpenAI draft request was not accepted.")
            body = response.read(131073)
        if len(body) > 131072:
            raise ProviderUnavailable("OpenAI draft response exceeded the limit.")
        result = json.loads(body)
        choices = result.get("choices") if isinstance(result, dict) else None
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ProviderUnavailable("OpenAI draft response was invalid.")
        choice = choices[0]
        message = choice.get("message")
        if choice.get("finish_reason") != "stop" or not isinstance(message, dict) or message.get("refusal") or message.get("tool_calls"):
            raise ProviderUnavailable("OpenAI draft response was incomplete or refused.")
        content = message.get("content")
        if not isinstance(content, str) or not 1 <= len(content.strip()) <= 32000:
            raise ProviderUnavailable("OpenAI draft response was invalid.")
        return content
    except (URLError, OSError, ValueError, TypeError):
        # Do not log exception text: HTTP errors can include provider content.
        raise ProviderUnavailable("OpenAI drafting unavailable; check the private provider configuration.") from None
