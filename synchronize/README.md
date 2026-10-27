# Synchronize fork of Dograh

Synchronize's fork of [dograh-hq/dograh](https://github.com/dograh-hq/dograh), BSD-2, kept equal to upstream except `synchronize/`. A
carrier forwards a customer's calls to a connection number, Dograh answers, and the Synchronize web app reads the call history through
Dograh's HTTP API with one organization key (the browser never sees it). Customers never see or edit prompts: the agent is content,
`workflows/*.json` (how to load it: `workflows/README.md`).

## Production

Railway project `synchronize-voice` (US East), separate from the product project. Official images, pinned: `dograhai/dograh-api` and
`dograhai/dograh-ui` 1.49.0, `pgvector/pgvector:pg17` with a volume, Railway's managed Redis, a Railway bucket for recordings (Dograh's
documented S3 settings: `ENABLE_AWS_S3`, `S3_BUCKET`, `S3_ENDPOINT_URL`, `S3_ADDRESSING_STYLE=virtual`, `S3_SIGNATURE_VERSION=s3v4`).
Variables follow `docker-compose.yaml`: `PUBLIC_BASE_URL` (the api's public URL), `DATABASE_URL` and `REDIS_URL` as references to the
Postgres and Redis services, `OSS_JWT_SECRET`, `TELEPHONY_WS_TOKEN_SECRET`, `FORWARDED_ALLOW_IPS=*` (the Railway proxy terminates TLS),
`ENABLE_SIGNUP=false` after the first account, `ENABLE_TELEMETRY=false`. Railway gotchas: a start command replaces the image entrypoint,
and a variable set from a pipe must not also redirect stdin.

One-time operator setup, all through Dograh's own UI and API (no code): sign up the first user and create an API key; add the OpenAI and
ElevenLabs provider connections and a model configuration (`POST /api/v1/model-connections/...`), set it as the default; load the
workflow; add the Twilio telephony configuration and the number routed to the workflow (Dograh pushes its inbound webhook to Twilio and
rejects unsigned requests). The Synchronize backend needs `DOGRAH_BASE_URL`, `DOGRAH_API_KEY`, `DOGRAH_CONNECTION_NUMBERS`.

## Measured

First real call, ElevenLabs Scribe v2 realtime + gpt-5-mini + Flash v2.5 (Han), 2.7 to 3.3 s per turn: Scribe commits a transcript after
about 1.7 s of silence (its default; Dograh does not expose `vad_silence_threshold_secs`), the language model needs about 0.5 s to its first
token (gpt-5-mini, gpt-4.1-mini, gpt-4.1 and gpt-5-nano all measured the same from the server), speech about 0.4 s. Speech-to-speech
(`gpt-realtime-2.1`) has no commit wait; the greeting may then be paraphrased, so listen for the AI notice. gpt-4.1 follows the prompt
better than gpt-5-mini: on the same prompts and 25 Korean turns scripted through the text-chat API (`POST /workflow/{id}/text-chat/sessions`),
13 turns broke a rule against 19; the final prompts on gpt-4.1 broke a rule in 10 of 25 Korean and 6 of 21 English turns (rules: more than two
sentences, polite-formal endings, restating the caller, asking for the phone number, leaking the instruction). gpt-4.1 is the language model
of both configurations. Not measured yet: Korean voice quality of OpenAI voices, realtime latency, any real call with these prompts.

## Upstream

`origin` = `SynchronizeAI/synchronize-voice-agent`, `upstream` = `dograh-hq/dograh`: `git fetch upstream && git merge --ff-only upstream/main`
on `main`, then rebase `synchronize/voice-agent-v1`.

`compose.dev-ports.yaml` and `dev-up.sh` run a local stack on loopback ports for development only.
