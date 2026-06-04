#!/usr/bin/env python3
"""
Full link checker for awesome-ai-agents-2026.
Checks ALL links in README.md, README.zh-CN.md, README.ja.md.
Reports dead/redirected/error links only; skips known-good badge hosts.
"""

import re
import sys
import time
import urllib.request
import urllib.error
import ssl
import concurrent.futures
from pathlib import Path
from collections import defaultdict

# ---- Config ----
FILES = ["README.md", "README.zh-CN.md", "README.ja.md"]
WORKERS = 12
TIMEOUT = 12
MAX_RETRIES = 2

# Skip these hosts (badges / shields / analytics that always return variable status)
SKIP_HOSTS = {
    "img.shields.io",
    "shields.io",
    "hits.seeyoufarm.com",
    "visitor-badge.glitch.me",
    "api.star-history.com",
    "madewithlove.now.sh",
    "forthebadge.com",
    "badgen.net",
    "flat.badgen.net",
}

# These URLs are known problematic (paywalls, anti-scrape, etc.) – treat as OK
KNOWN_OK_PATTERNS = [
    r"openai\.com",
    r"anthropic\.com",
    r"deepmind\.google",
    r"twitter\.com",
    r"x\.com",
    r"linkedin\.com",
    r"discord\.gg",
    r"discord\.com",
    r"t\.me",
    r"reddit\.com",
    r"news\.ycombinator\.com",
    r"gartner\.com",
    r"nature\.com",
    r"arxiv\.org",
    r"huggingface\.co",
    r"paperswithcode\.com",
    r"chatgpt\.com",
    r"claude\.ai",
    r"gemini\.google",
    r"cohere\.com",
    r"mistral\.ai",
    r"cohereinc",
    r"salesforce\.com",
    r"microsoft\.com",
    r"apple\.com",
    r"samsung\.com",
    r"oracle\.com",
    r"sap\.com",
    r"ibm\.com",
    r"deloitte\.com",
    r"venturebeat\.com",  # paywalled
    r"businessinsider\.com",
    r"bloomberg\.com",
    r"wsj\.com",
    r"nytimes\.com",
    r"techcrunch\.com",  # occasional 429
    r"theverge\.com",
    r"wired\.com",
]

KNOWN_OK_RE = [re.compile(p) for p in KNOWN_OK_PATTERNS]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def extract_links(text):
    """Return list of (url, line_number)."""
    results = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in re.finditer(r'\]\((https?://[^\s\)\"\']+)\)', line):
            results.append((m.group(1), i))
    return results


def should_skip(url):
    from urllib.parse import urlparse
    host = urlparse(url).netloc.lstrip("www.")
    if host in SKIP_HOSTS:
        return True
    for pat in KNOWN_OK_RE:
        if pat.search(url):
            return True
    return False


def check_url(url, retries=MAX_RETRIES):
    if should_skip(url):
        return url, "SKIP", 0
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS, method="HEAD")
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as resp:
                code = resp.status
                if code < 400:
                    return url, "OK", code
                # HEAD returned error, try GET
                req2 = urllib.request.Request(url, headers=HEADERS, method="GET")
                with urllib.request.urlopen(req2, timeout=TIMEOUT, context=SSL_CTX) as resp2:
                    return url, "OK" if resp2.status < 400 else "DEAD", resp2.status
        except urllib.error.HTTPError as e:
            if e.code == 405:  # Method not allowed for HEAD
                try:
                    req2 = urllib.request.Request(url, headers=HEADERS, method="GET")
                    with urllib.request.urlopen(req2, timeout=TIMEOUT, context=SSL_CTX) as resp2:
                        return url, "OK" if resp2.status < 400 else "DEAD", resp2.status
                except Exception:
                    pass
            if e.code in (429, 503) and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            return url, "DEAD", e.code
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(1)
                continue
            return url, "ERROR", str(e.reason)
        except Exception as e:
            if attempt < retries:
                time.sleep(1)
                continue
            return url, "ERROR", str(e)
    return url, "ERROR", "max retries"


def main():
    base = Path(__file__).parent.parent
    all_links = {}  # url -> list of (file, line)
    
    for fname in FILES:
        fpath = base / fname
        text = fpath.read_text(encoding="utf-8")
        for url, lineno in extract_links(text):
            all_links.setdefault(url, []).append((fname, lineno))
    
    unique_urls = list(all_links.keys())
    total = len(unique_urls)
    print(f"🔍 Checking {total} unique URLs across {len(FILES)} files...\n")
    
    results = {}
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        fut_map = {ex.submit(check_url, url): url for url in unique_urls}
        for fut in concurrent.futures.as_completed(fut_map):
            url, status, code = fut.result()
            results[url] = (status, code)
            done += 1
            if done % 50 == 0 or done == total:
                print(f"  [{done}/{total}] checked...", flush=True)
    
    # Report
    dead = []
    errors = []
    for url, (status, code) in sorted(results.items()):
        if status == "DEAD":
            for fname, lineno in all_links[url]:
                dead.append((fname, lineno, url, code))
        elif status == "ERROR":
            for fname, lineno in all_links[url]:
                errors.append((fname, lineno, url, code))
    
    ok_count = sum(1 for s, _ in results.values() if s == "OK")
    skip_count = sum(1 for s, _ in results.values() if s == "SKIP")
    dead_count = len(set(u for f, l, u, c in dead))
    err_count = len(set(u for f, l, u, c in errors))
    
    print(f"\n{'='*60}")
    print(f"SUMMARY: {ok_count} OK | {skip_count} skipped | {dead_count} dead | {err_count} errors")
    print(f"{'='*60}\n")
    
    if dead:
        print("❌ DEAD LINKS (HTTP 4xx/5xx):")
        for fname, lineno, url, code in sorted(dead):
            print(f"  [{code}] {fname}:{lineno}  {url}")
        print()
    
    if errors:
        print("⚠️  CONNECTION ERRORS:")
        for fname, lineno, url, code in sorted(errors):
            print(f"  [ERR] {fname}:{lineno}  {url}  ({code})")
        print()
    
    if not dead and not errors:
        print("✅ All checked links are reachable!")
    
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main())
