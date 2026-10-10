#!/usr/bin/env python3
"""Read a configured WhatsMax workspace and forward signed events to Odoo.

Run once from a scheduler. Uses the supplied template's real v1 API shapes.
Does not send messages to people. A private SQLite checkpoint stores hashes only.
"""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
from datetime import datetime
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Response:
    def __init__(self, status, content):
        self.status_code, self.content = status, content

    def json(self):
        return json.loads(self.content)


class HttpTransport:
    def _request(self, url, headers, timeout, data=None):
        request = Request(url, headers=headers, data=data)
        with build_opener(NoRedirect()).open(request, timeout=timeout[1]) as response:
            content = response.read(2_000_001)
            return Response(response.status, content)

    def get(self, url, headers, timeout, allow_redirects=False):
        return self._request(url, headers, timeout)

    def post(self, url, data, headers, timeout, allow_redirects=False):
        return self._request(url, headers, timeout, data=data)


def origin(value):
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Use exact HTTPS origins without paths or credentials")
    return value


def identifier(value):
    if isinstance(value, bool) or not str(value).isdigit() or not 0 < int(value) <= 2147483647:
        raise ValueError("Invalid provider identifier")
    return str(value)


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Timestamp required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not result.tzinfo:
        raise ValueError("Timestamp must include timezone")
    return result


class Sync:
    def __init__(self, provider, odoo, site, workspace, token, secret, since, database, http=None):
        self.provider, self.odoo = origin(provider), origin(odoo)
        self.site, self.workspace = identifier(site), identifier(workspace)
        if len(token) < 32 or len(secret) < 32 or any(c in token + secret for c in "\r\n"):
            raise ValueError("Missing credentials")
        self.token, self.secret, self.since, self.http = token, secret, timestamp(since), http or HttpTransport()
        self.db = database
        self.db.execute("CREATE TABLE IF NOT EXISTS forwarded (scope TEXT, event TEXT, digest TEXT, PRIMARY KEY(scope,event))")
        self.scope = hashlib.sha256((self.provider + self.workspace + self.odoo + self.site + since).encode()).hexdigest()

    def _get(self, url):
        parsed = urlsplit(url)
        if parsed.scheme + "://" + parsed.netloc != self.provider or parsed.username or parsed.password or parsed.fragment or not parsed.path.startswith("/api/v1/"):
            raise ValueError("Provider pagination attempted to change origin")
        response = self.http.get(url, headers={"Authorization": "Bearer " + self.token}, timeout=(5, 20), allow_redirects=False)
        if response.status_code != 200 or len(response.content) > 2_000_000:
            raise ValueError("Provider read failed")
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("Invalid provider response")
        return result

    def _pages(self, path):
        url, seen = self.provider + path, set()
        for _ in range(1000):
            if not url:
                return
            if url in seen:
                raise ValueError("Repeated pagination cursor")
            seen.add(url)
            # Never follow a provider-controlled link to another authenticated API.
            if urlsplit(url).path != path:
                raise ValueError("Pagination changed resource")
            page = self._get(url)
            if not isinstance(page.get("data"), list) or len(page["data"]) > 100 or not isinstance(page.get("links"), dict):
                raise ValueError("Invalid provider page")
            yield from page["data"]
            url = page["links"].get("next")
            if url is not None and not isinstance(url, str):
                raise ValueError("Invalid pagination link")
        raise ValueError("Page limit reached; sync not complete")

    def _emit(self, event):
        body = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(body).hexdigest()
        prior = self.db.execute("SELECT digest FROM forwarded WHERE scope=? AND event=?", (self.scope, event["eventRef"])).fetchone()
        if prior:
            if prior[0] != digest:
                raise ValueError("Previously forwarded provider event changed")
            return False
        stamp = str(int(time.time()))
        signature = "sha256=" + hmac.new(self.secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        response = self.http.post(self.odoo + "/companion/hub/inbound/" + self.site, data=body,
            headers={"Content-Type": "application/json", "X-Dojang-Timestamp": stamp, "X-Dojang-Signature": signature}, timeout=(5, 20), allow_redirects=False)
        if response.status_code != 200 or len(response.content) > 4096:
            raise ValueError("Odoo did not acknowledge the event; retry the same sync")
        acknowledgement = response.json()
        if not isinstance(acknowledgement, dict) or acknowledgement.get("accepted") is not True:
            raise ValueError("Odoo did not acknowledge the event; retry the same sync")
        self.db.execute("INSERT INTO forwarded VALUES (?,?,?)", (self.scope, event["eventRef"], digest))
        self.db.commit()
        return True

    def run(self):
        profile = self._get(self.provider + "/api/v1/auth/me")
        if identifier(profile.get("workspace_id")) != self.workspace or profile.get("demo_mode") is not False:
            raise ValueError("Provider token workspace mismatch or demo mode enabled")
        counts = {"messagesForwarded": 0, "statusesForwarded": 0, "duplicates": 0, "unsupportedMessages": 0}
        for conversation in self._pages("/api/v1/conversations"):
            if not isinstance(conversation, dict) or identifier(conversation.get("workspace_id")) != self.workspace:
                raise ValueError("Cross-workspace conversation rejected")
            cid, contact = identifier(conversation.get("id")), identifier(conversation.get("contact_id"))
            for message in self._pages("/api/v1/conversations/" + cid + "/messages"):
                if not isinstance(message, dict) or identifier(message.get("conversation_id")) != cid:
                    raise ValueError("Cross-conversation message rejected")
                if timestamp(message.get("created_at")) < self.since:
                    continue
                mid = identifier(message.get("id"))
                event = None
                if message.get("direction") == "in":
                    if message.get("channel") not in ("sms", "whatsapp") or message.get("type") != "text" or not isinstance(message.get("body"), str) or not 1 <= len(message["body"].strip()) <= 1500:
                        counts["unsupportedMessages"] += 1
                        continue
                    event = {"type": "message.received", "workspaceRef": self.workspace, "eventRef": "whatsmax:message:" + mid,
                        "contactRef": contact, "channel": message["channel"], "text": message["body"]}
                elif message.get("direction") == "out" and message.get("status") in ("delivered", "read", "failed") and message.get("provider_message_id"):
                    status = "failed" if message["status"] == "failed" else "delivered"
                    event = {"type": "delivery.status", "workspaceRef": self.workspace, "eventRef": "whatsmax:status:" + mid + ":" + status,
                        "providerRef": message["provider_message_id"], "status": status}
                if event:
                    sent = self._emit(event)
                    counts[("messagesForwarded" if event["type"] == "message.received" else "statusesForwarded") if sent else "duplicates"] += 1
        return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", help="Private literal dotenv file; otherwise use process environment")
    args = parser.parse_args(argv)
    if args.env_file:
        from private_env import load_private_env
        settings = load_private_env(args.env_file)
    else:
        settings = os.environ
    # Provider signature enforcement must be configured in WhatsMax itself.
    # This explicit gate prevents treating an unverified provider intake as trusted.
    if settings.get("DOJANG_HUB_PROVIDER_INGRESS_VERIFIED") != "true":
        raise ValueError("Verify upstream provider webhook authentication before enabling sync")
    path = Path(settings["DOJANG_HUB_SYNC_STATE"])
    if not path.exists():
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) or path.stat().st_mode & 0o077:
        raise ValueError("Checkpoint must be a private regular file")
    with sqlite3.connect(path) as database:
        task = Sync(settings["DOJANG_HUB_WHATSMAX_ORIGIN"], settings["DOJANG_HUB_ODOO_ORIGIN"], settings["DOJANG_HUB_SITE_ID"],
            settings["DOJANG_HUB_WORKSPACE_ID"], settings["DOJANG_HUB_WHATSMAX_TOKEN"], settings["DOJANG_HUB_WEBHOOK_SECRET"],
            settings["DOJANG_HUB_SYNC_SINCE"], database)
        print(json.dumps({"status": "completed", **task.run()}))


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError, UnicodeError, OSError, sqlite3.Error):
        raise SystemExit("Sync incomplete. Check private configuration and provider/Odoo availability; no secrets or message content were logged.")
