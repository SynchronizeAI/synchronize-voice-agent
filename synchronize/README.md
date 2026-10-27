# Synchronize fork of Dograh

This is Synchronize's fork of [dograh-hq/dograh](https://github.com/dograh-hq/dograh). It is the voice backend for the AI
phone-consultation feature: a carrier forwards a customer's calls to a connection number, Dograh answers with a Pipecat
agent, and the Synchronize web app reads call history from Dograh's HTTP API.

Nothing in this fork has been verified with a real call. Every telephony and model provider used while developing it was a
fake: carrier webhooks were simulated, provider keys were placeholders, and model calls went to nothing. Treat
"connectivity works" as unproven until someone places a real call.

## Rules for this fork

- `main` stays equal to `upstream/main`. Synchronize work lives on `synchronize/voice-agent-v1` (local, not pushed).
- Reuse Dograh as it is. The Synchronize backend is the only client of the Dograh API and authenticates with one
  org-scoped `X-API-Key` per workspace; the browser never sees it. Dograh source changes need a reason written next to
  the change. Everything added so far lives in `synchronize/`.
- Customers never see or edit prompts, greeting or tone. The agent is content, not code: `synchronize/workflows/*.json`
  (load instructions in its README); the STT/LLM/TTS choice is `synchronize/model-configuration.md`.

## Syncing upstream

Remotes: `origin` = `SynchronizeAI/synchronize-voice-agent`, `upstream` = `dograh-hq/dograh`. Procedure (not yet run):

```bash
git fetch upstream
git checkout main && git merge --ff-only upstream/main && git push origin main
git checkout synchronize/voice-agent-v1 && git rebase main
git submodule update --init --recursive      # pipecat is pinned by the commit recorded in main
```

After a rebase, re-run `synchronize/dev-up.sh` (it applies new Alembic migrations) and replay the recorded fixtures against
the new build to see what drifted.

## Run it locally

Needs Docker, Python 3.13 with `uv`, and the `pipecat` submodule initialised (`git submodule update --init --recursive`).
Everything heavy (venv, logs, pid files, env file) lives in one directory outside the repo; call it `$SYNC_VOICE_DEV_DIR`.

```bash
export SYNC_VOICE_DEV_DIR=/home/enoch/sync-merge-work/.agent-tmp/dograh-up   # any directory outside the repo

# 1. Infra: Postgres, Redis, MinIO on loopback ports (skip if the shared sync-voice-dograh project is already up).
docker compose -p sync-voice-dograh -f docker-compose-local.yaml -f synchronize/compose.dev-ports.yaml up -d postgres redis minio

# 2. venv with Dograh's requirements (the script installs into the active interpreter and
#    installs pipecat editable from the submodule).
uv venv --python 3.13 "$SYNC_VOICE_DEV_DIR/venv"
VIRTUAL_ENV="$SYNC_VOICE_DEV_DIR/venv" PATH="$SYNC_VOICE_DEV_DIR/venv/bin:$PATH" bash scripts/setup_requirements.sh

# 3. Env file.
cp synchronize/dev.env.example "$SYNC_VOICE_DEV_DIR/api.env"   # then replace OSS_JWT_SECRET and DOGRAH_DEVOPS_SECRET

# 4. Start / stop. dev-up.sh runs `alembic upgrade head`, starts uvicorn and the arq worker, waits for /api/v1/health.
synchronize/dev-up.sh
synchronize/dev-down.sh
```

`dev-up.sh` restarts a previous run of itself. It refuses to start when its port is held by something else, and
`dev-down.sh` stops only the processes recorded in `$SYNC_VOICE_DEV_DIR/run/*.pid`; it never touches containers.
Logs are in `$SYNC_VOICE_DEV_DIR/logs/` (`uvicorn.log`, `arq.log`, plus the raw `*.out` files).

What runs: `uvicorn api.app:app` (127.0.0.1, one worker, no reload) and the arq worker (`api.tasks.arq.WorkerSettings`),
which runs the post-call completion job (final extraction, integrations, billing step) and the sweepers. Not started:
`ari_manager`, `campaign_orchestrator` (no Asterisk, no campaigns) and the Next.js UI.

### Ports

| Port | What |
| --- | --- |
| 56800 | Dograh API (`/api/v1/...`, OpenAPI at `/openapi.json`) |
| 56432 / 56379 | Postgres / Redis |
| 56900 / 56901 | MinIO S3 API / console |
| 56801 | second API process in S3 (presigned URL) mode, only when started by hand, see below |

### Hosted services off

- **MPS (services.dograh.com)** has no disable flag. The only knob is `MPS_API_URL`; `dev.env.example` points it at the
  closed loopback port `http://127.0.0.1:1`, so any MPS call fails immediately and nothing leaves the host. Consequences,
  observed: signup still succeeds, but org bootstrap cannot create a default model configuration, so
  `GET /model-connections/default` returns `{"model_configuration_uuid": null}` until a BYO provider connection and model
  configuration are created and set as default. Bootstrap is not retried on the API-key path.
- **Telemetry.** Sentry starts only when `SENTRY_DSN` is set (and in OSS mode only with `ENABLE_TELEMETRY=true`), PostHog only
  when `POSTHOG_API_KEY` is set, Langfuse only when `LANGFUSE_*` are set. Leave them unset and keep
  `ENABLE_TELEMETRY="false"`. With the env above, the open sockets of both processes are loopback only (Postgres, Redis,
  MinIO). The log line `Error connecting to cloudflared: ... name resolution` is the tunnel probe failing on its DNS name;
  it is harmless and `/health` reports `tunnel_url: null`.
- **Signup** stays enabled (`ENABLE_SIGNUP=true`): `POST /api/v1/auth/signup` is the only way Dograh creates an organization.
  Use an e-mail domain that is not special-use; `.invalid` and `.local` are rejected, `*.example` is accepted.

### Storage

The bundled MinIO bucket is public and its URLs are unsigned and never expire. That is acceptable for local development
only. For presigned URLs run an API process with the S3 backend pointed at MinIO (or real S3) by adding to the environment:

```
ENABLE_AWS_S3=true AWS_ACCESS_KEY_ID=<minio key> AWS_SECRET_ACCESS_KEY=<minio secret> S3_BUCKET=voice-audio
S3_REGION=us-east-1 S3_ENDPOINT_URL=http://127.0.0.1:56900 S3_SIGNATURE_VERSION=s3v4 S3_ADDRESSING_STYLE=path
```

and calling `GET /api/v1/s3/signed-url?key=...&storage_backend=s3` on it.

### Providers

Creating a provider connection makes Dograh call `GET {base_url}/models` to validate the key. For local work use a fake key
with a `connection_settings.base_url` that points at a loopback stub, not at a real provider. Creating or updating a
telephony phone number makes Dograh call the carrier (ownership check, webhook push), which cannot succeed with fake
credentials; a phone-number row for development has to be inserted through `db_client.create_phone_number`.

## Seeded data

The shared dev database holds clearly synthetic data (caller `+1415555xxxx`, run name `WR-TEL-IN-SEED0001`, a 6 s tone as
the "recording", `extra.synthetic.synthetic = true` on the run). It is not a call. Recorded request/response pairs and the
list of what is real versus seeded are in `/home/enoch/sync-merge-work/.agent-tmp/voice-fixtures/README.md`.
