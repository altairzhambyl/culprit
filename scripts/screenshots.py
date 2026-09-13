#!/usr/bin/env python3
"""Regenerate the dashboard screenshots used in the README, from a real investigation.

Nothing here is staged or mocked: the script starts `culprit serve`, runs the churn demo
investigation through the offline (`scripted`) policy, approves the pull request in the browser
like a human would, and photographs the three moments the README shows.

    pip install playwright && playwright install chromium
    python scripts/screenshots.py                    # -> docs/screenshot-*.png

Prerequisites: `culprit demo init` has been run (or pass --repo to point somewhere else).
"""

from __future__ import annotations

import argparse
import contextlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOTS = {
    "screenshot-approval": "the one question Culprit asks",
    "screenshot-dashboard": "the finished investigation",
    "screenshot-report": "the evidence: every commit it re-ran",
}


def free_port() -> int:
    with contextlib.closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for(url: str, timeout: float = 60.0) -> None:
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    raise SystemExit(f"server never came up at {url}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="demo/churn-model", help="repository to investigate")
    ap.add_argument("--out", default="docs", help="directory to write the PNGs into")
    ap.add_argument("--width", type=int, default=1500)
    ap.add_argument("--height", type=int, default=940)
    ap.add_argument("--url", default=None, help="use an already-running dashboard instead of starting one")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("pip install playwright && playwright install chromium") from None

    out = (ROOT / args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    server = None
    base = args.url
    if base is None:
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = {**os.environ, "CULPRIT_MODEL_PROVIDER": os.environ.get("CULPRIT_MODEL_PROVIDER", "scripted")}
        server = subprocess.Popen(
            [sys.executable, "-m", "culprit.cli", "serve", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        wait_for(f"{base}/api/health")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(
                viewport={"width": args.width, "height": args.height}, device_scale_factor=2
            )
            # The README is read in light mode; pin it so the shots never depend on the host theme.
            page.add_init_script("try{localStorage.setItem('culprit-theme','light')}catch(e){}")
            page.goto(base)
            page.wait_for_timeout(1200)
            if page.is_hidden("#repoInput"):
                page.click("#newBtn")
                page.wait_for_timeout(500)
            page.fill("#repoInput", args.repo)
            page.click("#startBtn")
            print(f"investigating {args.repo} …")

            def status() -> str:
                return page.evaluate(
                    "async () => { const j = await (await fetch('/api/runs')).json();"
                    " return j.length ? j[0].status : 'none'; }"
                )

            approved = False
            for _ in range(300):
                page.wait_for_timeout(1000)
                st = status()
                if st == "awaiting_human" and not approved:
                    page.wait_for_timeout(1200)
                    page.screenshot(path=str(out / "screenshot-approval.png"))
                    print("  captured screenshot-approval.png")
                    page.click("#approveBtn")
                    approved = True
                elif st == "completed":
                    page.wait_for_timeout(2500)
                    page.screenshot(path=str(out / "screenshot-dashboard.png"))
                    print("  captured screenshot-dashboard.png")
                    page.evaluate("document.getElementById('logCard').open = true")
                    page.eval_on_selector("#commitsCard", "e => e.scrollIntoView({block:'start'})")
                    page.wait_for_timeout(900)
                    page.screenshot(path=str(out / "screenshot-report.png"))
                    print("  captured screenshot-report.png")
                    break
                elif st == "failed":
                    page.screenshot(path=str(out / "screenshot-failed.png"))
                    raise SystemExit("the investigation failed; see docs/screenshot-failed.png")
            else:
                raise SystemExit("timed out waiting for the investigation to finish")
            browser.close()
    finally:
        if server is not None:
            server.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                server.wait(timeout=10)

    for name, what in SHOTS.items():
        path = out / f"{name}.png"
        print(f"{'✔' if path.exists() else '✖'} {path.relative_to(ROOT)} — {what}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
