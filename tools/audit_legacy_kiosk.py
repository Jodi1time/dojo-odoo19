#!/usr/bin/env python3
"""Inventory Paul's original HTML locally without uploading its source or secrets.

Usage: python tools/audit_legacy_kiosk.py ~/Downloads/Kiosk-responsive-2.html
Prints JSON containing pages, function names, and API paths only.
This is static source inventory, not a live backend test.
"""
import argparse
import json
import re
from pathlib import Path

PAGE_RE = re.compile(r'<(?:div|section)\b[^>]*\bid=["\\']page-([a-zA-Z0-9_-]+)["\\']', re.I)
FUNC_RE = re.compile(r'\b(?:async\s+)?function\s+([a-zA-Z_$][\w$]*)\s*\(')
API_RE = re.compile(r'/api/(?:[a-zA-Z0-9_./?-]|\$\{[^}]*\})+')
SECRET_RE = re.compile(r'(?i)(?:token|password|secret|authorization)')

def inventory(text):
    pages = sorted(set(PAGE_RE.findall(text)))
    funcs = sorted(set(FUNC_RE.findall(text)))
    endpoints = sorted(set(API_RE.findall(text)))
    # Avoid displaying any query-string values or embedded device tokens.
    paths = sorted({p.split('?')[0] for p in endpoints if not SECRET_RE.search(p)})
    return {
        "source_type": "legacy_html_static_inventory",
        "verified_live": False,
        "pages": pages,
        "functions": funcs,
        "api_paths": paths,
        "function_count": len(funcs),
        "api_path_count": len(paths),
        "note": "Source presence does not establish backend implementation, permission safety or working behavior.",
    }

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path, help="Local HTML file, not a URL")
    parser.add_argument("--out", type=Path, help="Optional local JSON output")
    args = parser.parse_args()
    text = args.html.read_text(encoding="utf-8")
    result = json.dumps(inventory(text), indent=2)
    if args.out:
        args.out.write_text(result + "\n", encoding="utf-8")
        print(f"Saved static inventory to {args.out}")
    else:
        print(result)

if __name__ == "__main__":
    main()
