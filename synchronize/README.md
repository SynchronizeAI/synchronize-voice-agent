# Synchronize fork of Dograh

This is Synchronize's fork of [dograh-hq/dograh](https://github.com/dograh-hq/dograh). It is the voice backend for the AI
phone-consultation feature: a carrier forwards a customer's calls to a connection number, Dograh answers with a Pipecat
agent, and the Synchronize web app reads call history from Dograh's HTTP API.

**No real phone call has been placed.** Carrier webhooks were simulated. The local test organization talks to OpenAI with a
dev key that lives outside the repo (browser calls with a synthetic caller, see "Checks without a microphone").

## Rules for this fork

- `main` stays equal to `upstream/main`. Synchronize work lives on `synchronize/voice-agent-v1` (local, not pushed).
- Reuse Dograh as it is. The Synchronize backend is the only client of the Dograh API and authenticates with an org-scoped
  `X-API-Key`; the browser never sees it. Dograh source changes need a reason written next to the change. Everything added
  so far lives in `synchronize/`, and each glue file says in its first lines what Dograh cannot do.
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

## Run it locally

Dograh's own flow is `docs/contribution/setup.mdx` (`scripts/start_services_dev.sh`, `cd ui && npm run dev`). Differences used
here: loopback ports, and venv / env file / logs outside the repo in one directory, `$SYNC_VOICE_DEV_DIR`.

```bash
export SYNC_VOICE_DEV_DIR=/home/enoch/sync-merge-work/.agent-tmp/dograh-up
# 1. Postgres, Redis, MinIO on loopback ports (the shared sync-voice-dograh project already runs them)
docker compose -p sync-voice-dograh -f docker-compose-local.yaml -f synchronize/compose.dev-ports.yaml up -d postgres redis minio
# 2. venv: Dograh's stock script installs into the active interpreter (pipecat editable from the submodule)
uv venv --python 3.13 "$SYNC_VOICE_DEV_DIR/venv"
VIRTUAL_ENV="$SYNC_VOICE_DEV_DIR/venv" PATH="$SYNC_VOICE_DEV_DIR/venv/bin:$PATH" bash scripts/setup_requirements.sh
# 3. $SYNC_VOICE_DEV_DIR/api.env = api/.env.example with these keys changed
# 4. API + arq worker on 127.0.0.1 (applies migrations first)
synchronize/dev-up.sh
```

`api.env` keys that differ from `api/.env.example`:

```
UVICORN_BASE_PORT=56800
BACKEND_API_ENDPOINT=http://127.0.0.1:56800
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:56432/postgres
REDIS_URL=redis://:redissecret@127.0.0.1:56379
OSS_JWT_SECRET=<random>            DOGRAH_DEVOPS_SECRET=<random>
MPS_API_URL=http://127.0.0.1:1     # Dograh's hosted MPS has no off switch, only this URL: calls fail at once
ENABLE_TELEMETRY=false             # and leave SENTRY_DSN, POSTHOG_API_KEY, LANGFUSE_* unset
MINIO_ENDPOINT=127.0.0.1:56900     MINIO_PUBLIC_ENDPOINT=http://127.0.0.1:56900
```

Ports: API `56800` (`/api/v1/...`, `/openapi.json`), UI `56300`, Postgres `56432`, Redis `56379`, MinIO `56900`/`56901`. Not
started: `ari_manager`, `campaign_orchestrator` (no Asterisk, no campaigns). With this env both processes hold loopback
sockets only. MPS off means: signup works, org bootstrap cannot create a default model configuration, so
`GET /model-connections/default` is `{"model_configuration_uuid": null}` until a provider connection and model configuration
are created and set as default (bootstrap is not retried on the API-key path). `POST /auth/signup` is the only way Dograh
creates an organization; e-mail domains `.invalid`/`.local` are rejected, `*.example` works.

Creating a provider connection makes Dograh call the provider's `GET {base_url}/models` to validate the key. Creating or
updating a telephony phone number makes Dograh call the carrier (ownership check, webhook push); with fake credentials that
fails, so development phone-number rows are inserted with `db_client.create_phone_number`.

## Public URL for inbound telephony (not set; no tunnel approved)

A carrier must reach the API, and the media WebSocket it is told to dial. These are the settings that carry the public address
(`api/utils/common.py:get_backend_endpoints`, `api/utils/tunnel.py`):

- **`BACKEND_API_ENDPOINT`** (or `PUBLIC_BASE_URL`, from which it derives): the origin Dograh pushes to the carrier as the inbound
  webhook (`<origin>/api/v1/telephony/inbound/run`), puts in the `<Stream url=...>` / WebSocket address of every call
  (`https` gives `wss`, `http` gives `ws`) and in status callbacks. Set it to the public `https://` origin. While it is a
  local or private address Dograh first looks for a cloudflared sidecar at `http://cloudflared:2000/metrics` (a docker
  service name) and only then uses the configured value; a public hostname or IP is used as is.
- **`FORWARDED_ALLOW_IPS`**: the address of the proxy or tunnel in front of uvicorn. Without it `request.url` stays `http://` and
  Twilio's URL-signature check rejects real calls (comment in `scripts/run_web.sh`).
- **`MINIO_PUBLIC_ENDPOINT`** (derives from `PUBLIC_BASE_URL`): base of the recording and transcript URLs handed to clients.
- **`TELEPHONY_WS_TOKEN_SECRET`** (+ `TELEPHONY_WS_TOKEN_ENFORCE=true`): signs the media WebSocket URL; without it the id triple in
  the URL is the only protection once the port is public.

The webhook URL reaches the carrier only when a phone number is created or updated in Dograh (or is typed into the carrier
console), so re-save the number after the address changes.

## Browser test (UI) and WebRTC

Stock UI flow, loopback only: in `ui/`, `.env` with `BACKEND_URL=http://127.0.0.1:56800`, `NEXT_PUBLIC_BACKEND_URL=http://127.0.0.1:56800`,
`UI_PORT=56300`, then `npm ci && npm run dev -- --hostname 127.0.0.1`. The browser calls the API at `NEXT_PUBLIC_BACKEND_URL`, so with
SSH forwarding forward the same port numbers (`ssh -L 56300:127.0.0.1:56300 -L 56800:127.0.0.1:56800 <host>`) and open
`http://localhost:56300` (`localhost` counts as a secure context, which the microphone needs). Sign up or sign in, open the
agent, "Test Audio" > "Run Test". `next dev` fetches the Geist font from fonts.googleapis.com when `layout.tsx` first compiles
(`next/font/google`); nothing else in the UI or API contacts a third party while telemetry keys are unset.

First-test caveat (not a blocker): in OpenAI Realtime mode the greeting is only a prompt to the model, so the AI-disclosure sentence is not guaranteed
word for word (it came out verbatim in the two realtime test runs, but nothing enforces it); a legal gate needs a pre-recorded greeting or separate STT/TTS.
`gpt-live-1` did not start a session with the dev key (greeting never spoken, run ended in `pipeline_error`).

WebRTC: signaling is a WebSocket on the API port (forwarded); **media is UDP and is not carried by SSH forwarding.** The server
offers its non-loopback IPv4 addresses on ephemeral UDP ports (aioice never offers 127.0.0.1; `ENVIRONMENT=local` filters
nothing), and both browser and server also use the hard-coded `stun:stun.l.google.com:19302` unless `FORCE_TURN_RELAY=true`
(`api/routes/webrtc_signaling.py:get_ice_servers`, `ui/.../useWebSocketRTC.tsx`). So media flows only if the laptop can reach the
host's address over UDP. The alternative that needs nothing but TCP forwarding is Dograh's TURN: `ENABLE_COTURN=true`,
`TURN_HOST`, `TURN_SECRET`, `FORCE_TURN_RELAY=true` and the `coturn` service of `docker-compose.yaml` (config template
`deploy/templates/turnserver.remote.conf.template`), with the TURN port forwarded too. Not tried.

## Checks without a microphone

- **Text chat**: UI "Test Chat" tab, or `POST /api/v1/workflow/{id}/text-chat/sessions`, `.../sessions/{run_id}/messages`, `.../end`.
  Runs the real LLM, node transitions and the post-call extraction of the node active at the end. It does not run the call
  disposition classifier (`call_disposition` stays `end_call`) and records no audio.
- **Draft or published**: text chat and the browser "Run Test" both create runs with `use_draft=true`: they run the draft when one
  exists, else the published version. Phone calls always run the published version.
- **Headless browser call**: Chromium in the local `mcr.microsoft.com/playwright` image with `--use-fake-device-for-media-stream
  --use-file-for-fake-audio-capture=<wav>` signs in, clicks "Run Test" and speaks a recorded caller. It exercises signaling,
  WebRTC, STT, LLM, TTS, recording and extraction. Scripts: `/home/enoch/sync-merge-work/.agent-tmp/dograh-up/tools/`.

## Seeded data and fixtures

The shared dev database holds synthetic data (caller `+1415555xxxx`, run `WR-TEL-IN-SEED0001`, `extra.synthetic.synthetic = true`).
Recorded requests and responses, and what is real versus seeded, are in
`/home/enoch/sync-merge-work/.agent-tmp/voice-fixtures/README.md`.
