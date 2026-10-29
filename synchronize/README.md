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
workflow and enter its settings (`workflows/README.md`); add the Twilio telephony configuration and the number routed to the workflow (Dograh pushes its inbound webhook to Twilio and
rejects unsigned requests). The Synchronize backend needs `DOGRAH_BASE_URL` and `DOGRAH_API_KEY`.

## Measured

End of the caller's speech to the first sound, Korean sentences at 8 kHz from a US server (clean synthetic callers, so network and noise are not included):
`gpt-realtime-2.1` 1.66 s median (its server VAD waits 0.5 s; 1.30 s with reasoning effort `minimal`, a setting Dograh does not pass); ElevenLabs
Scribe v2 realtime + gpt-4.1 + Flash v2.5 about 2.7 s (Scribe commits after 1.83 s, its default; Dograh does not expose `vad_silence_threshold_secs`;
gpt-4.1 first sentence 0.62 to 0.70 s; speech 0.14 s); Deepgram Nova-3 `ko` returns the final text 0.3 to 0.5 s after the end of speech,. A real Deepgram call logged STT 0.3 to 0.4 s, LLM 0.6 to 1.0 s,
speech 0.11 to 0.13 s; a real realtime call logged 0.8 to 1.4 s to the first sound. The bot speaks 6.2 syllables per second, which is not slow,
so a slow feel comes from long replies and the wait before them. `gpt-4.1` ends 2027-01-14 and its replacement `gpt-6-luna` took 1.3 s to a first
sentence in the same probe, so plan the swap.

Conversation quality is judged with a simulated caller that talks like a real one (fillers, corrections, details nobody asked for) through the
text-chat API (`POST /workflow/{id}/text-chat/sessions`), each reply graded for restating the caller, asking about the substance of the matter, and
length. The first real Deepgram call showed the failure the scripted checks missed (restating every turn, interrogating the caller's subject); after the
step-per-job rewrite, 12 Korean calls gave restating in 13 of 40 replies against 46 of 54 before, and questions about the matter 0 against 16. What
remains is mostly the three short sentences of the goodbye. Not measured: how the OpenAI and ElevenLabs Korean voices sound compared by ear, and any
real call with the new structure.

## Upstream

`origin` = `SynchronizeAI/synchronize-voice-agent`, `upstream` = `dograh-hq/dograh`: `git fetch upstream && git merge --ff-only upstream/main`
on `main`, then rebase `synchronize/voice-agent-v1`.
