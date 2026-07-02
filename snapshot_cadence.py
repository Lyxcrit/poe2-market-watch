#!/usr/bin/env python3

import hashlib
import json
import time
from datetime import datetime, timezone

import requests

URL = "https://poe2scout.com/api/poe2/Leagues/runes/SnapshotPairs"
POLL_SECONDS = 60

def fetch_hash():
    headers = {
        "User-Agent": "poe2-snapshot-cadence-checker/0.1 contact:lyxcrit@gmail.com",
        "Accept": "application/json",
    }

    r = requests.get(URL, headers=headers, timeout=30)
    r.raise_for_status()

    raw = r.content
    digest = hashlib.sha256(raw).hexdigest()

    try:
        data = r.json()
        row_count = len(data) if isinstance(data, list) else len(data.get("data", []))
    except Exception:
        row_count = "unknown"

    return digest, row_count, len(raw)

def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def main():
    print(f"Watching: {URL}")
    print(f"Polling every {POLL_SECONDS}s")
    print()

    last_hash = None
    last_change = None

    while True:
        try:
            digest, row_count, size = fetch_hash()

            if last_hash is None:
                last_hash = digest
                last_change = time.time()
                print(f"[{now()}] Initial snapshot hash={digest[:12]} rows={row_count} bytes={size}")

            elif digest != last_hash:
                elapsed = time.time() - last_change
                print(
                    f"[{now()}] SNAPSHOT CHANGED after {elapsed/60:.1f} minutes "
                    f"hash={digest[:12]} rows={row_count} bytes={size}"
                )
                last_hash = digest
                last_change = time.time()

            else:
                elapsed = time.time() - last_change
                print(
                    f"[{now()}] No change. Same snapshot for {elapsed/60:.1f} minutes "
                    f"hash={digest[:12]}"
                )

        except Exception as e:
            print(f"[{now()}] ERROR: {e}")

        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
