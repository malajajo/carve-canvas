#!/usr/bin/env bash
# Wrapper that runs the locally-installed Blender with the locally-extracted
# system libraries (no root needed on this machine).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LD_LIBRARY_PATH="$ROOT/tools/syslibs/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$ROOT"/tools/blender-*-linux-x64/blender "$@"
