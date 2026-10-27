# Model configuration for the phone consultation (STT / LLM / TTS)

What to set as each customer organization's default model configuration (`PUT /api/v1/model-connections/default`).
Written on 2026-10-26 from this repository's code, the catalogue its API serves (`GET /api/v1/model-connections/catalog`)
and the providers' own documentation.

**No benchmark was run and no call (real or simulated) was placed.** Nothing here measures Korean accuracy, latency or voice
quality at 8 kHz. "Supported" means "a provider document or this code says so". Anything no source settled is marked
**UNCERTAIN**. The first test to run is at the end.

## Recommended when OpenAI is the only key: OpenAI Realtime

Speech-to-speech: one session hears the caller and speaks, with no separate STT or TTS (`mode: "realtime"`). Workflow
transitions arrive as tool calls; the post-call extraction and disposition run on the separate text LLM.

| Role | Service | Settings (Korean customer / English customer) |
| --- | --- | --- |
| `realtime` | provider `openai` | `model: "gpt-realtime-2.1"`, `language: "ko"` / `"en"` (pins input transcription), `voice`: `marin`, `cedar` or `alloy`, chosen by listening |
| `llm` | provider `openai` | `model: "gpt-5-mini"` |

Both roles share one OpenAI provider connection. `gpt-realtime-2.1-mini` is the cheaper sibling.

* **Only OpenAI path where Korean can be set.** The `language` setting is passed to the session's input transcription, with
  a comment that auto-detect "misfires on short/noisy telephony audio" (`api/services/pipecat/service_factory.py:1617-1622`).
* **`gpt-realtime-2.1`** updates `gpt-realtime-2` with "improved alphanumeric recognition, silence and noise handling, and
  interruption behavior" at the same price (OpenAI's model page).
* **`gpt-5-mini`** gets `reasoning_effort: minimal` and no temperature (`service_factory.py:1406-1412`). OpenAI marks this
  code's default `gpt-4.1` as Deprecated. This model only writes the extraction fields and the call outcome.
* **Variant to compare: `gpt-live-1`** (full duplex, delegates to a backend Responses model, default `gpt-5.4-mini`, billed
  separately). OpenAI's voice table lists a Korean voice `noeul`, the only place its docs name Korean for a realtime model.
  Here `language` is hidden for it and the catalogue offers only `marin` and `cedar`; the voice field takes custom ids, so
  `noeul` can be typed (UNCERTAIN: not tried). Needs a paid tier.
* **8 kHz.** Twilio and Telnyx legs are 8 kHz and the pipeline runs at that rate. No audio format is set on the OpenAI
  session, so it uses `audio/pcm` at 24 kHz: caller audio is resampled up (pipecat `services/openai/realtime/llm.py:1340-1346`)
  and the reply down. OpenAI documents G.711 mu-law sessions and says GPT-Live takes raw G.711 at 8 kHz over WebSocket; this
  code never selects it. No OpenAI document says how accurate its models are on 8 kHz phone speech.

### Why not OpenAI's separate STT + LLM + TTS

English would work (its payload was exercised against a stub). Korean would not:

* **STT is forced to English.** Only the model is passed (`service_factory.py:668`); pipecat's OpenAI STT defaults to
  `Language.EN` and always sends `language` (`pipecat/services/openai/stt.py:130-137,176-178`); the catalogue has no language
  field for it. Read from code, not run.
* **STT is not streaming.** pipecat's own P99 time-to-final-transcript constants: 2.01 s OpenAI REST, 0.35 s Deepgram
  (`pipecat/services/stt_latency.py`; measured by pipecat, language and rate not stated).
* **TTS voice is fixed to `alloy`** (only the model is passed, `service_factory.py:932-940`); OpenAI calls its voices
  "optimized for English"; PCM output is 24 kHz.
* A fix is small (a `language` field for OpenAI STT, passing `voice` to OpenAI TTS) but it is a change to Dograh source.

## Better quality, needs other keys

No measurement backs "better"; these are setups whose Korean support is documented on every side and settable here.

**Recommended:** Deepgram `nova-3-general` (STT, `language: "ko"` or `"en"`) + OpenAI `gpt-5-mini` (LLM) + ElevenLabs
`eleven_flash_v2_5` (TTS, a Korean-capable `voice` id). Deepgram lists Korean for Nova-3 and is this code's default STT
(`api/services/configuration/defaults.py:25`); ElevenLabs lists Korean for Flash v2.5 (~75 ms model latency) and this code
asks for `pcm_8000` at 8 kHz and sends no `language_code` (pipecat `elevenlabs/tts_base.py:430`). Use an explicit STT
language, not `multi`.

Then, in order: Cartesia `ink-whisper` + `sonic-3.6` (`language: "ko"` on both; not `ink-2`, English only); ElevenLabs
`scribe_v2_realtime` (set `language: "ko"`, the default is `en`) + Flash v2.5; Azure Speech (`ko-KR`) or Google (`chirp_3`,
`chirp_3_hd` with a `ko-KR-Chirp3-HD-*` voice); Soniox `stt-rt-v5`; Gemini `gemini-3.5-flash` instead of `gpt-5-mini`.

## Payloads

All under `/api/v1/model-connections`, authenticated with the organization's `X-API-Key`.

1. `POST /provider-connections`, one per vendor: `{"name": "openai", "provider": "openai", "credentials": {"api_key": "..."},
   "connection_settings": {}}`. Answer: `{uuid, name, provider, connection_settings, configured_credentials, revision,
   is_active}`; credentials are never echoed. Creating one calls the provider to validate the key.
2. `POST /model-configurations`. OpenAI realtime (not yet exercised):

   ```json
   {"name": "consultation-ko",
    "configuration": {"version": 3, "mode": "realtime",
      "llm": {"provider_connection_uuid": "<openai>", "settings": {"model": "gpt-5-mini"}},
      "realtime": {"provider_connection_uuid": "<openai>",
                   "settings": {"model": "gpt-realtime-2.1", "voice": "marin", "language": "ko"}}}}
   ```

   Chained (exercised against a stub): `"mode": "pipeline"` with `llm`, `stt` (`{"model": "nova-3-general", "language":
   "ko"}`) and `tts` (`{"model": "eleven_flash_v2_5", "voice": "<id>", "speed": 1.0}`), each with its
   `provider_connection_uuid`. `settings` may not hold credentials, connection fields or `provider`; the provider's schema
   validates them. Pipeline without `stt` and `tts` answers 422; realtime requires `realtime`; `llm` is required in both.
3. `PUT /default` with `{"model_configuration_uuid": "<uuid>"}`. If Dograh's bootstrap fails an organization has no
   default, so always set one explicitly.
4. A workflow can override part of it (`workflow_configurations.model_configuration_override`); typed here, not exercised, and
   the workflows in `workflows/` do not set it.

## Known gaps

* **Korean quality of any OpenAI voice model is UNCERTAIN.** OpenAI's pages for `gpt-realtime-2`, `-2.1` and `gpt-live-1`
  name no languages. Which voice sounds natural in Korean on a phone line is a listening decision.
* **The greeting is not guaranteed word for word in realtime mode.** This code tells the model to say it "exactly as written"
  (`api/services/pipecat/realtime/static_greeting.py`); OpenAI's GPT-Live guide says "the model may paraphrase the greeting".
  If the exact words ever matter, use a pre-recorded greeting (`greeting_type: "audio"` on the start node) or a separate
  STT/TTS setup, where TTS speaks the text verbatim. The workflows here use a text greeting.
* **`allow_greeting_interrupt: false` mutes the caller for 35 s in realtime mode.** Dograh mutes the caller until the greeting
  is reported as played, and in realtime mode that report never arrives (`first_output_at=None`, outcome `timed_out`/`closed`),
  so the caller was silent for the whole timeout (seen in four test runs). The workflows set it to `true`.
* **Caller quotes** come from the session's input transcription (default `gpt-realtime-whisper`), not from what the model
  heard; check the Korean quotes in the first test.
* **Deepgram cannot be the only vendor for Korean:** Flux and Aura-2 TTS list no Korean; Nova-3 STT does. There is no Nova-3
  phone-call model, so Nova-3 on 8 kHz Korean is the main unknown.
* **Defaults are English** (ElevenLabs STT, Cartesia, Azure, the default voices); nothing sets Korean on its own.
* **Google** lists Korean on `chirp_3` and `telephony`, not under this code's default names `latest_long`/`latest_short`.
* **Numbers:** ElevenLabs says Flash v2.5 does not normalize numbers by default.
* **Data leaves the platform** in every setup (audio, text or transcript go to the vendors); Korean data handling is an open
  legal gate.
* `reasoning_effort: minimal` is sent for every model whose name contains `gpt-5`; newer snapshots may reject it.

## First test to run

Place the same scripted caller (one Korean, one English, over an 8 kHz carrier leg or Dograh's own test call) through
`gpt-realtime-2.1`, `gpt-live-1` and one setup from the second list. Compare word error on caller turns, time from end of
speech to first audio, whether the greeting came out verbatim, the Korean quotes in the stored extraction, and listen to the
voices.

## Sources

OpenAI: https://developers.openai.com/api/docs/guides/realtime, `.../guides/realtime-conversations`, `.../guides/realtime-sip`,
`.../models/gpt-realtime-2`, `.../models/gpt-realtime-2.1`, `.../models/gpt-live-1`, `.../models/gpt-4.1`,
`.../guides/live-conversations` (voices), `.../guides/live-prompting` (greeting may be paraphrased),
`.../guides/realtime-models-prompting` and `.../guides/voice-prompting` (pin the language), `.../guides/speech-to-text`,
`.../guides/text-to-speech`. Deepgram: https://developers.deepgram.com/docs/models-languages-overview and
`.../docs/tts-models`. ElevenLabs: https://elevenlabs.io/docs/overview/models and
`.../docs/overview/capabilities/speech-to-text`. Cartesia: https://docs.cartesia.ai/build-with-cartesia/tts-models/latest.
Google: https://ai.google.dev/gemini-api/docs/models, https://docs.cloud.google.com/text-to-speech/docs/chirp3-hd,
https://docs.cloud.google.com/speech-to-text/docs/speech-to-text-supported-languages. Azure:
https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support. Soniox:
https://soniox.com/docs/stt/concepts/supported-languages.
