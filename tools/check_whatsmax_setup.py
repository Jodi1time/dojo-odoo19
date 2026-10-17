#!/usr/bin/env python3
"""Check a fresh WhatsMax installation without sending messages or changing Odoo.

config: inspect a private dotenv file on the deployment operator's machine.
connection: use only GET requests with the same provider settings as the sync.
No credentials, URLs, contact identities or message bodies appear in the report.
"""
import argparse
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import urlsplit

from sync_whatsmax_hub import HttpTransport, identifier, origin, timestamp
from private_env import load_private_env


def check(name, passed, hint, failure="blocked"):
    return {"check": name, "status": "pass" if passed else failure, "hint": hint}


def is_secret(value, minimum=32):
    return (isinstance(value, str) and minimum <= len(value) <= 4096
            and not any(c.isspace() for c in value)
            and not any(marker in value.lower() for marker in ("replace", "example", "changeme", "<", ">")))


def valid_origin(value):
    try:
        parsed = urlsplit(origin(value))
        return (parsed.hostname not in ("localhost", "127.0.0.1", "::1")
                and "replace" not in parsed.hostname
                and not parsed.hostname.endswith((".test", ".example", ".invalid")))
    except (ValueError, TypeError, AttributeError):
        return False


def service_url(value, schemes, database=False):
    try:
        parsed = urlsplit(value)
        return (parsed.scheme in schemes and bool(parsed.hostname) and not parsed.fragment
                and (not database or (bool(parsed.username) and bool(parsed.password)
                                      and len(parsed.path.strip("/")) > 0))
                and "<" not in value and ">" not in value and "password@" not in value)
    except (ValueError, TypeError, AttributeError):
        return False


def report(checks, successful_status):
    blocked = any(item["status"] == "blocked" for item in checks)
    return {"status": "blocked" if blocked else successful_status,
            "checks": checks, "liveMessagingVerified": False, "messagesSent": 0}


def config_report(values):
    encryption = values.get("APP_ENCRYPTION_KEY", "")
    checks = [
        check("public_origin", valid_origin(values.get("APP_URL")), "Set APP_URL to the final HTTPS origin, without a path or trailing slash."),
        check("auth_origin", bool(values.get("APP_URL")) and values.get("NEXTAUTH_URL") == values.get("APP_URL"), "NEXTAUTH_URL must equal APP_URL."),
        check("production_mode", values.get("APP_ENV") == "production", "Use APP_ENV=production so WhatsApp intake requires an app secret."),
        check("demo_disabled", values.get("APP_DEMO_MODE") == "false", "Set APP_DEMO_MODE=false; showcase mode blocks API writes."),
        check("demo_seed_disabled", values.get("SEED_DEMO_DATA") == "false", "Set SEED_DEMO_DATA=false to avoid shared demo identities."),
        check("auth_secret", is_secret(values.get("AUTH_SECRET")), "Generate a private AUTH_SECRET with at least 32 characters."),
        check("encryption_key", bool(re.fullmatch(r"[a-fA-F0-9]{64}", encryption)) and len(set(encryption)) > 1,
              "Use a randomly generated 64-hex APP_ENCRYPTION_KEY; retain it across deployments."),
        check("mysql_url", service_url(values.get("DATABASE_URL"), ("mysql",), database=True), "WhatsMax needs MySQL credentials and a database name; do not point it at Odoo's PostgreSQL."),
        check("redis_url", service_url(values.get("REDIS_URL"), ("redis", "rediss")), "Set the reachable private Redis endpoint used by web, worker and scheduler."),
        check("mail_transport", values.get("MAIL_TRANSPORT") == "smtp", "MAIL_TRANSPORT=log does not deliver invitation or reset emails; configure SMTP before testing them.", failure="warning"),
        check("storage", values.get("STORAGE_DRIVER") in ("local", "s3"), "Choose local persistent storage or the supported S3-compatible backend."),
        {"check": "runtime_services", "status": "pending", "hint": "Verify migrations, web, worker, scheduler, database, Redis and persistent uploads on the actual host."},
        {"check": "channel_and_license", "status": "pending", "hint": "Complete the licensed install and configure a real provider channel in WhatsMax."},
    ]
    if values.get("MAIL_TRANSPORT") == "smtp":
        checks.append(check("smtp_settings", all(values.get(key) for key in ("MAIL_HOST", "MAIL_PORT", "MAIL_FROM_ADDRESS")), "Complete SMTP host, port, sender and any credentials required by your mail service."))
    if values.get("STORAGE_DRIVER") == "local":
        checks.append({"check": "persistent_uploads", "status": "pending", "hint": "Persist and share public/uploads and public/branding wherever the processes need them; container-local storage is temporary."})
    if values.get("STORAGE_DRIVER") == "s3":
        checks.append(check("object_storage_settings", all(values.get(key) for key in ("AWS_BUCKET", "AWS_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")), "Configure the storage driver's bucket, region and credentials; verify uploads on the host."))
    return report(checks, "configuration_checks_passed")


class ProbeFailure(Exception):
    def __init__(self, code):
        self.code = code


def connection_report(values, http=None):
    http = http or HttpTransport()
    checks = []
    provider = values.get("DOJANG_HUB_WHATSMAX_ORIGIN")
    token = values.get("DOJANG_HUB_WHATSMAX_TOKEN")
    try:
        provider = origin(provider)
        workspace = identifier(values.get("DOJANG_HUB_WORKSPACE_ID"))
        if not is_secret(token):
            raise ValueError("Missing token")
    except (ValueError, TypeError, AttributeError):
        return report([check("connection_settings", False, "Set the exact HTTPS WhatsMax origin, numeric workspace ID and private bearer token used by sync.")], "read_connection_verified")

    def read(path):
        try:
            response = http.get(provider + path, headers={"Authorization": "Bearer " + token},
                                timeout=(5, 20), allow_redirects=False)
        except HTTPError as exc:
            raise ProbeFailure("http_" + str(exc.code)) from None
        except (OSError, TimeoutError, ValueError):
            raise ProbeFailure("transport_failed") from None
        if response.status_code != 200:
            raise ProbeFailure("http_" + str(response.status_code))
        if len(response.content) > 2_000_000:
            raise ProbeFailure("response_too_large")
        try:
            data = response.json()
        except (ValueError, UnicodeError):
            raise ProbeFailure("invalid_json") from None
        if not isinstance(data, dict):
            raise ProbeFailure("unexpected_response")
        return data

    def page(data, path):
        if not isinstance(data.get("data"), list) or len(data["data"]) > 100 or not isinstance(data.get("links"), dict):
            raise ProbeFailure("unexpected_pagination")
        following = data["links"].get("next")
        if following is not None:
            if not isinstance(following, str):
                raise ProbeFailure("unsafe_pagination")
            try:
                parsed = urlsplit(following)
                if (parsed.scheme + "://" + parsed.netloc != provider or parsed.path != path
                        or parsed.username or parsed.password or parsed.fragment):
                    raise ValueError("Changed destination")
            except ValueError:
                raise ProbeFailure("unsafe_pagination") from None
        return data["data"]

    stage = "workspace_identity"
    try:
        profile = read("/api/v1/auth/me")
        if identifier(profile.get("workspace_id")) != workspace or profile.get("demo_mode") is not False:
            raise ProbeFailure("workspace_mismatch_or_demo_mode")
        checks.append(check(stage, True, "Bearer token resolves to the configured workspace with demo mode off."))
        stage = "conversation_api"
        conversations = page(read("/api/v1/conversations"), "/api/v1/conversations")
        for conversation in conversations:
            if not isinstance(conversation, dict) or identifier(conversation.get("workspace_id")) != workspace:
                raise ProbeFailure("workspace_mismatch")
            identifier(conversation.get("id"))
            identifier(conversation.get("contact_id"))
        checks.append(check(stage, True, "Conversation read scope and the actual vendor pagination contract are verified."))
        stage = "message_api"
        if conversations:
            cid = identifier(conversations[0]["id"])
            path = "/api/v1/conversations/" + cid + "/messages"
            messages = page(read(path), path)
            for message in messages:
                if not isinstance(message, dict) or identifier(message.get("conversation_id")) != cid:
                    raise ProbeFailure("conversation_mismatch")
                identifier(message.get("id"))
                timestamp(message.get("created_at"))
            checks.append(check(stage, True, "One thread's message API contract is verified; no message content is logged."))
            if not messages:
                checks.append({"check": "incoming_test_message", "status": "pending", "hint": "The sampled thread is empty. Receive a controlled parent message before testing the Odoo handoff."})
        else:
            checks.append({"check": stage, "status": "pending", "hint": "Fresh workspace has no conversation yet. Receive a controlled parent message, then rerun."})
    except ProbeFailure as exc:
        checks.append({"check": stage, "status": "blocked", "code": exc.code,
                       "hint": "Check service availability, token scope, workspace selection and the supplied vendor API version. No upstream body was logged."})
    except (ValueError, TypeError, KeyError, AttributeError):
        checks.append(check(stage, False, "Provider identifiers or timestamps do not match the supplied vendor contract."))

    checks.extend([
        {"check": "provider_ingress", "status": "pending", "hint": "Independently verify provider webhook authentication. A configuration flag is not evidence of a valid provider signature."},
        {"check": "odoo_handoff", "status": "pending", "hint": "Run signed sync and verify the same parent message in Odoo and Companion."},
        {"check": "outbound_delivery", "status": "pending", "hint": "Read checks cannot prove messages:write, a configured sender, recipient delivery or an authenticated status relay. Use one reviewed, controlled reply."},
    ])
    return report(checks, "read_connection_verified")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("config", "connection"))
    parser.add_argument("--env-file", help="Private literal dotenv file; otherwise read process environment")
    args = parser.parse_args(argv)
    try:
        values = load_private_env(Path(args.env_file)) if args.env_file else dict(os.environ)
        result = config_report(values) if args.mode == "config" else connection_report(values)
    except (OSError, ValueError, UnicodeError):
        result = report([check("env_file", False, "Use a private UTF-8 regular file (chmod 600), unique literal KEY=value lines and no shell expansion.")], "blocked")
    print(json.dumps(result, indent=2))
    return 1 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
