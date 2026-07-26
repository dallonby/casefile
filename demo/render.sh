#!/usr/bin/env bash
# Rebuild cast + frontpage GIF with a real loop hold on the last frame.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python3 demo/build_cast.py

# agg still clamps long idles; we patch the GIF after.
agg --cols 100 --rows 30 --font-size 14 --idle-time-limit 30 \
  demo/casefile-continuity.cast demo/casefile-continuity.gif

# GIF hack: force last-frame delay (agg drops long cast holds)
python3 demo/build_cast.py --pad-gif --hold 15

echo "done: demo/casefile-continuity.gif"
