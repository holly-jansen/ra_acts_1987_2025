
#!/usr/bin/env python3
"""Collect Republic Acts through an authorized Chrome CDP session."""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "https://www.congress.gov.ph/legislative-documents/republic-acts"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-page", type=int, default=1)
    ap.add_argument("--max-pages", type=int, default=30)
    ap.add_argument("--html-dir", default="./ra_archive")
    args = ap.parse_args()

    archive = Path(args.html_dir)
    archive.mkdir(parents=True, exist_ok=True)

    seen = set()

    # Include already captured pages in duplicate detection.
    for old in sorted(archive.glob("page_*.html")):
        content = old.read_text(encoding="utf-8")
        seen.add(hashlib.sha256(content.encode("utf-8")).hexdigest())

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(
            "http://127.0.0.1:9222",
            timeout=30000
        )

        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()

        for number in range(args.start_page, args.start_page + args.max_pages):
            destination = archive / f"page_{number:04d}.html"

            if destination.exists():
                print("PRESERVED:", destination, flush=True)
                continue

            url = BASE if number == 0 else f"{BASE}?page={number}"

            print("OPEN:", url, flush=True)

            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000
            )

            page.wait_for_timeout(4000)

            status = response.status if response else None
            title = page.title()
            html = page.content()

            print("HTTP:", status, "TITLE:", title, flush=True)

            if status != 200 or "just a moment" in title.lower():
                print("STOP: Browser access unavailable.")
                break

            if "republic act" not in html.lower():
                print("STOP: Republic Acts content not detected.")
                break

            digest = hashlib.sha256(html.encode("utf-8")).hexdigest()

            if digest in seen:
                print("STOP: Identical HTML page detected.")
                break

            # Save a temporary source, not a validated archive page.
            temporary = archive / f".pending_{number:04d}.html"
            temporary.write_text(html, encoding="utf-8")

            command = [
                sys.executable,
                "ra_pipeline.py",
                "capture-browser",
                "--page", str(number),
                "--html-dir", str(archive),
                "--from-file", str(temporary)
            ]

            result = subprocess.run(command, check=False)

            if result.returncode != 0:
                print("STOP: Existing Republic Acts parser rejected page.")
                break

            seen.add(digest)
            temporary.unlink()
            print("SAVED:", destination, flush=True)

        print("Collection stopped. Validate saved pages before building.")

        # Disconnect without intentionally closing the user's Chrome window.
        browser.close()


if __name__ == "__main__":
    main()
