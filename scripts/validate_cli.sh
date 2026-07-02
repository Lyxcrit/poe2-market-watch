#!/usr/bin/env bash
set -euo pipefail

python3 poe2checker.py --help >/dev/null
python3 poe2watcher.py --help >/dev/null

echo "CLI help checks passed."
