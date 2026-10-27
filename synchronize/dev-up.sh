#!/usr/bin/env bash
# Missing in Dograh: scripts/start_services_dev.sh hard-codes `uvicorn --host 0.0.0.0` and keeps venv/logs inside the repo.
# This runs the same API + arq worker on 127.0.0.1 with env file, venv, pids and logs under $SYNC_VOICE_DEV_DIR.
# Stop: kill $(cat $SYNC_VOICE_DEV_DIR/run/uvicorn.pid $SYNC_VOICE_DEV_DIR/run/arq.pid)
set -euo pipefail
D=${SYNC_VOICE_DEV_DIR:?set SYNC_VOICE_DEV_DIR (holds api.env and venv/)}; cd "$(dirname "$0")/.."; mkdir -p "$D/run" "$D/logs"
set -a; . "$D/api.env"; set +a; export PATH="$D/venv/bin:$PATH"
kill $(cat "$D"/run/uvicorn.pid "$D"/run/arq.pid 2>/dev/null) 2>/dev/null || true
alembic -c api/alembic.ini upgrade head
LOG_FILE_PATH="$D/logs/uvicorn.log" setsid nohup uvicorn api.app:app --host 127.0.0.1 --port "$UVICORN_BASE_PORT" >>"$D/logs/uvicorn.out" 2>&1 & echo $! >"$D/run/uvicorn.pid"
LOG_FILE_PATH="$D/logs/arq.log" setsid nohup python -m arq api.tasks.arq.WorkerSettings --custom-log-dict api.tasks.arq.LOG_CONFIG >>"$D/logs/arq.out" 2>&1 & echo $! >"$D/run/arq.pid"
