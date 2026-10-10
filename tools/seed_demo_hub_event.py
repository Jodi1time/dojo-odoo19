"""Send one explicitly synthetic parent event to the disposable local rehearsal."""
import hashlib
import hmac
import json
import os
import time
from urllib.request import Request, build_opener

from sync_whatsmax_hub import NoRedirect


def main():
    if os.environ.get("DOJANG_ODOO_URL") != "http://127.0.0.1:8069" or not os.environ.get("DOJANG_ODOO_DATABASE", "").startswith("dojang_demo_"):
        raise ValueError("Only the disposable loopback demo is allowed")
    site = os.environ["DOJANG_HUB_SITE_ID"]
    if not site.isdigit():
        raise ValueError("Invalid fixture site")
    event = {"type": "message.received", "workspaceRef": "11", "eventRef": "interactive-synthetic-parent-100",
        "channel": "sms", "contactRef": "42", "text": "Synthetic parent report: My child cannot attend this class. Could you help with a makeup?"}
    body = json.dumps(event).encode()
    stamp = str(int(time.time()))
    signature = hmac.new(os.environ["DOJANG_HUB_DEMO_WEBHOOK_SECRET"].encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    request = Request("http://127.0.0.1:8069/companion/hub/inbound/" + site, data=body,
        headers={"Content-Type": "application/json", "X-Dojang-Timestamp": stamp, "X-Dojang-Signature": "sha256=" + signature})
    with build_opener(NoRedirect()).open(request, timeout=10) as response:
        result = json.loads(response.read(4096))
    if result.get("accepted") is not True:
        raise ValueError("Demo event was not accepted")
    print("Signed synthetic parent event saved. No external provider was contacted.")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError, OSError):
        raise SystemExit("Synthetic event setup failed; inspect the local demo configuration.")
