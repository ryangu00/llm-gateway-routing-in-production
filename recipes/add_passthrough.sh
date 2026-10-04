#!/usr/bin/env bash
# Edit a candidate by default; --apply explicitly enables restart and probes.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
exec "${PYTHON:-python3}" "$HERE/passthrough.py" "$@"
