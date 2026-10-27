#!/usr/bin/env bash
# Start the Dograh API and the arq worker on 127.0.0.1 against an already running
# Postgres/Redis/MinIO. Local development only; nothing here is a deployment path.
#
# Required: SYNC_VOICE_DEV_DIR, a directory outside this repo that holds
#   api.env   the env file (see synchronize/README.md for the exact keys)
#   venv/     the Python venv with api/requirements.txt installed
# Logs and pid files go into the same directory (logs/, run/).
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV_DIR="${SYNC_VOICE_DEV_DIR:?set SYNC_VOICE_DEV_DIR to the dev directory that holds api.env and venv/}"
ENV_FILE="${DOGRAH_ENV_FILE:-$DEV_DIR/api.env}"
VENV="${DOGRAH_VENV:-$DEV_DIR/venv}"
RUN_DIR="$DEV_DIR/run"
LOG_DIR="$DEV_DIR/logs"

[[ -f "$ENV_FILE" ]] || { echo "missing env file: $ENV_FILE" >&2; exit 1; }
[[ -x "$VENV/bin/uvicorn" ]] || { echo "missing venv with uvicorn: $VENV" >&2; exit 1; }

set -a && . "$ENV_FILE" && set +a
export PATH="$VENV/bin:$PATH"
PORT="${UVICORN_BASE_PORT:?UVICORN_BASE_PORT must be set in $ENV_FILE}"

mkdir -p "$RUN_DIR" "$LOG_DIR"
"$(dirname "${BASH_SOURCE[0]}")/dev-down.sh" >/dev/null

if (ss -ltn "sport = :$PORT" | grep -q LISTEN); then
  echo "port $PORT is already taken by something this script did not start" >&2
  exit 1
fi

cd "$BASE_DIR"
alembic -c api/alembic.ini upgrade head

# --workers 1 and no --reload: one process, restart explicitly after code changes.
# 127.0.0.1 only: the dev API must not be reachable from the network.
LOG_FILE_PATH="$LOG_DIR/uvicorn.log" setsid nohup uvicorn api.app:app --host 127.0.0.1 --port "$PORT" --workers 1 \
  >>"$LOG_DIR/uvicorn.out" 2>&1 &
echo $! >"$RUN_DIR/uvicorn.pid"

LOG_FILE_PATH="$LOG_DIR/arq.log" setsid nohup python -m arq api.tasks.arq.WorkerSettings --custom-log-dict api.tasks.arq.LOG_CONFIG \
  >>"$LOG_DIR/arq.out" 2>&1 &
echo $! >"$RUN_DIR/arq.pid"

for _ in $(seq 1 60); do
  if [[ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/v1/health" || true)" == "200" ]]; then
    echo "dograh api healthy at http://127.0.0.1:$PORT (uvicorn pid $(<"$RUN_DIR/uvicorn.pid"), arq pid $(<"$RUN_DIR/arq.pid"))"
    echo "logs: $LOG_DIR"
    exit 0
  fi
  sleep 1
done
echo "dograh api did not become healthy; see $LOG_DIR/uvicorn.out" >&2
exit 1
