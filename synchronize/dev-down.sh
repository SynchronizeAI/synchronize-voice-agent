#!/usr/bin/env bash
# Stop the processes started by dev-up.sh (only those, by pid file). Containers are never touched.
set -euo pipefail

DEV_DIR="${SYNC_VOICE_DEV_DIR:?set SYNC_VOICE_DEV_DIR to the dev directory used by dev-up.sh}"
RUN_DIR="$DEV_DIR/run"

for name in uvicorn arq; do
  pidfile="$RUN_DIR/$name.pid"
  [[ -f "$pidfile" ]] || continue
  pid="$(<"$pidfile")"
  if kill -0 "$pid" 2>/dev/null; then
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 15); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -0 "$pid" 2>/dev/null && { kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true; }
    echo "stopped $name ($pid)"
  fi
  rm -f "$pidfile"
done
