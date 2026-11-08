# Dograh 1.49.0 → 1.51.0

What changes for Synchronize's line when the official images go from `dograh-v1.49.0` to `dograh-v1.51.0`. Every reference is
`file:line` at the `dograh-v1.51.0` tag unless it says 1.49. docs.dograh.com tracks main and was not used. Release notes: `CHANGELOG.md:3-69`.
Images: `dograhai/dograh-api:1.51.0` (`sha256:715398c8…1fe0`) and `dograhai/dograh-ui:1.51.0` (`sha256:ec16ad0f…dfb9`), both pushed
2026-11-04, amd64 and arm64 (Docker Hub, read 2026-11-08). Rollback targets: `dograhai/dograh-api:1.49.0` (`sha256:89fab005…7402`),
`dograhai/dograh-ui:1.49.0` (`sha256:293b355f…7e67`).

## Database

Two Alembic revisions after 1.49.0's head `b3c1d9e4f7a2`. The api image runs `alembic -c api/alembic.ini upgrade head` on every start
before it serves (`scripts/start_services_docker.sh:30`, the image `CMD`, `api/Dockerfile:178`), so deploying the api image migrates.

| revision | what it does | downgrade |
|---|---|---|
| `c82d4f6a9b10` unify tool messages (`api/alembic/versions/c82d4f6a9b10_unify_tool_messages.py`) | Rewrites every row of `tools` (archived ones too, all organizations, `FOR UPDATE`, `:108-134`): the old per-type fields (`customMessage`, `customMessageType`, `customMessageRecordingId` for HTTP tools; `messageType`, `customMessage`, `audioRecordingId` for End Call/Transfer; `message` for agent transfer) become `definition.messages = [{"event": "start", ...}]` and are removed from `config` (`:34-67`). Our two English End Call tools (`messageType: custom`) keep their goodbye as one start text message. | Exists (`:70-105`, `:141-142`) but **refuses any tool with more than one message or a delayed message** (`:73-81`), archived tools included, and names it by its `tools.id` (`:121-127`). The new caller-work tools have a delayed message, so they and any other such tool must be cut to one start message (or the database restored) before this downgrade can run. |
| `d6a83e2b19f0` index pending completions (`api/alembic/versions/d6a83e2b19f0_index_pending_workflow_completions.py`) | `CREATE INDEX CONCURRENTLY idx_workflow_runs_pending_completion` on `workflow_runs (id)` where the run is completed and `extra.workflow_completion.status` is pending/running (`:16-34`), for the completion sweeper. No data change. | Drops the index (`:37-44`). |

The 1.49.0 image cannot start on a database at `d6a83e2b19f0`: its `alembic upgrade head` does not know that revision and the start
script runs with `set -e` (`scripts/start_services_docker.sh:2`). Image rollback therefore needs the Alembic downgrade first, run
from the 1.51.0 image (the runbook's R steps).

## Configuration

`docker-compose.yaml` is identical between the tags, so no Railway variable changes. One new variable, `DOGRAH_DEPLOYMENT_ID`
(`api/constants.py:78-80`), defaults to `oss` and is only read by the hosted MPS key client (`api/services/mps_service_key_client.py:692,735`).
Python dependencies moved (Pipecat 1.15, `tuner-pipecat-sdk` 0.3.0, `jsonschema`), all inside the image.

## Fields Synchronize uses

- **Pre-call fetch.** `api/services/pipecat/pre_call_fetch.py` is unchanged. The response's `initial_context` is merged into the call
  (`api/services/pipecat/event_handlers.py:153-166`) and persisted on the run (`:226-229`), so `caller_name` and `caller_history`
  are readable from the run afterwards. A pre-call `greeting_override` does not reach the greeting: the engine reads it once at
  construction (`api/services/workflow/pipecat_engine.py:174-176`), before the fetch is merged.
- **Variables in prompts.** Greeting, prompts, extraction prompts and disposition descriptions render with `render_template` against
  the call context (`pipecat_engine.py:421-424`, `:1092-1134`). The syntax is `{{name}}` or `{{name | fallback:text}}`
  (`api/utils/template_renderer.py:14`); `initial_context.` is optional (`:192-196`). A missing variable without a fallback becomes an
  empty string (`:249-250`); `{{name | fallback:}}` with an empty fallback does not match the pattern and stays in the prompt as
  literal braces. There is no conditional text, so a caller's name cannot go into the fixed greeting with an unknown caller still
  hearing today's greeting.
- **Webhook node payload.** `api/tasks/run_integrations.py` is unchanged: the body is still `{"workflow_run_id": "<digits>",
  "call_disposition": ...}`. What changed is the order: integrations now run after the durable final extraction
  (`api/tasks/workflow_completion.py:34-69`). In 1.49 the webhook could fire while the hangup extraction was still in flight
  (1.49 `api/tasks/workflow_completion.py`, comment before the PBX write-back).
- **Extraction timing (#915).** A voice call no longer extracts during teardown: the engine stamps the call status as the fallback
  disposition (`pipecat_engine.py:1544-1556`) and hands the work to the completion worker (`run_pipeline.py:948`,
  `api/services/workflow/final_extraction.py:116-231`; 60 s budget, 15 s per attempt, retries after 2 s and 5 s, `:42-44`). The
  classified disposition replaces the fallback only if the run still carries the fallback (`api/db/workflow_completion_client.py:119-122`).
  Until the worker finishes, a run read from the API shows the call status (`user_hangup`, ...) as `call_disposition` and
  `mapped_call_disposition`; a failed extraction leaves it there. A lost job is re-queued by the sweeper after 30 minutes
  (`api/db/workflow_completion_client.py:145-166`). Text chat still extracts inline (`text_chat_runner.py:704`).
- **Run detail.** `gathered_context` keys are unchanged. `logs` gains `conversation` and `final_extraction` (`pipecat_engine.py:921-928`).
  `logs.realtime_feedback_events` now carries the greeting as a bot-text event with `speech_id`, `outcome`, `interrupted`, `played_ms`
  and caller speech heard during the greeting as a user transcription with `background_greeting_id`
  (`api/services/pipecat/realtime_feedback_observer.py:117-190`, `log_speech` and `log_greeting`). Additive for Synchronize's tolerant models.
- **`mapped_call_disposition`.** Same mapping (`disposition_mapping`), now applied twice: to the fallback at hangup, and to the
  classified code by the worker. A dispositions list holds at most 50 codes, codes match `^[A-Za-z][A-Za-z0-9_-]*$`, 1,000 characters
  per description and 4,000 in total (`api/schemas/workflow_configurations.py:30-49`).
- **HTTP tool schema (#890).** Tools carry `definition.messages`: one `start` message and `delayed` messages with `after_ms`
  (1–120,000), text or recording, up to 20 variants per timing (`api/schemas/tool.py:519-602`). `HttpApiConfig`, `EndCallConfig` and
  `TransferCallConfig` now forbid unknown keys (`:99`, `:182`, `:373`): a client sending the old `customMessage` gets HTTP 422. The
  start message plays when the call begins, a delayed one only if the request is still running, and both stop when the result
  arrives (`api/services/workflow/tool_messages.py:47-124`, `tool_execution.py`). Dograh stores `definition.model_dump()`
  (`api/services/tool_management.py:209`), so a definition written with every default is read back byte-equal. Presets render from
  the call context and are coerced to their type (`api/services/workflow/tools/custom_tool.py:173-275`); `workflow_run_id` is
  run-owned and cannot come from outside (`api/services/workflow/initial_context.py:10-21`, `run_pipeline.py:693`). The result reaches
  the model as `{"status", "status_code", "data"}`, an HTTP error as `status: error` (`custom_tool.py:497-600`).
- **Tool-call recovery (#920, #926, #945).** When the model writes a tool call as spoken text, voice calls now recover and run it
  and keep control text out of speech (`api/services/pipecat/spoken_tool_call_guard.py:1-10`). Text chat does not
  (`spoken_output=False`, `text_chat_runner.py:606`), so text-chat evals cannot measure this.
- **Call QA transcript.** A tool call appears as `[tool_call]: <function name>` only; its arguments and result are left out
  (`api/services/workflow/qa/conversation.py:51-55`, `:78-82`), and the QA prompt renders no call context, only its own placeholders
  (`api/services/workflow/qa/analysis.py:324-330`). QA cannot tell a faithfully relayed record from an invented one.
- **Final extraction input.** The post-call extraction reads the user, assistant and tool messages only, not the system prompt
  (`api/services/workflow/conversation_history.py:117-160`), so a caller name known only from `initial_context` is extracted only if it
  was said or confirmed in the call.
- **Text-chat API.** Unchanged for us: `initial_context` on session create, reserved keys dropped (`api/routes/workflow_text_chat.py:41-48`,
  `:191`). Text-chat runs are `call_type` `outbound` by default (`api/db/models.py:669-673`).
- **Workflow update.** `PUT /workflow/{id}` replaces the stored settings document (`api/services/workflow/configuration_policy.py:36-40`)
  and still saves a draft. Tools named on a node must not share a function name there (`api/services/workflow/tool_name_validation.py:18-80`);
  the function name is the lower-cased tool name (`custom_tool.py:41-44`).

## Behaviour callers notice

- **Silence after the greeting (#951).** Until the caller first speaks, the idle wait after the greeting is at most 3 s
  (`api/services/pipecat/call_monitor_processor.py:233-243`), then the configured `max_user_idle_timeout` (10 s default). The first idle
  prompt asks whether the caller is still there and the second ends the call (`api/services/workflow/pipecat_engine_callbacks.py:31-45`).
  A silent caller is now hung up about 6 s after the greeting instead of about 20 s. Not measured on our lines.
- **Greeting interruption (#911).** New Start Call fields `allow_greeting_interrupt` (default true) and `greeting_interrupt_min_words`
  (default 2, cascade only) (`api/services/workflow/dto.py:368-392`). The defaults equal 1.49's behaviour (1.49
  `api/services/pipecat/greeting.py`, two words while the greeting is pending), so the templates leave them out.
- **Call opening (#913, #950).** One opening path for all transports; early caller speech is kept.

## Operator UI

Tools → an HTTP tool's messages editor: "When the tool starts" and delayed entries with an "After" delay
(`ui/src/components/tools/ToolMessagesEditor.tsx:80-127`); End Call tools use the same editor instead of "End Message type". Start Call
gains "Allow Greeting Interruption" / "Minimum Words to Interrupt" and `allow_interrupt` is relabelled "Allow Interruptions After
Greeting". Telephony gains WhatsApp. The 1.49 UI still sends the old tool fields, so do not edit tools between the api and ui deploys.

## Breaking for us

Nothing in Synchronize or this fork sends the old tool message fields (`golive.py` writes `messages`). The migration downgrade
limit above is the one rollback trap. The 3 s post-greeting idle cap is the one caller-facing change.

## Railway steps (project `synchronize-voice`, environment `production`)

Exact commands: `RUNBOOK-20261108.md` steps 1–4 and R2–R5. In order:

1. Back up the voice Postgres (logical `pg_dump -Fc` to the owner's machine, verified by reading every data block with
   `pg_restore -f /dev/null`, since `pg_restore -l` reads only the table of contents), and confirm `alembic current` is `b3c1d9e4f7a2`.
2. `dograh-api` → image `dograhai/dograh-api:1.51.0`. Watch the deploy log for `Running upgrade b3c1d9e4f7a2 -> c82d4f6a9b10` and
   `-> d6a83e2b19f0`; healthy when `GET /api/v1/health` answers 200 (the compose health check) and `alembic current` is `d6a83e2b19f0`.
3. `dograh-ui` → image `dograhai/dograh-ui:1.51.0`; healthy when the UI root answers 200.
4. Check the two English End Call tools now show their goodbye under "When the tool starts".

Rollback, newest state first: workflow changes with `golive.py --rollback`; cut every tool with a delayed message or more than
one message to its start message, archived tools included (`GET /tools/` hides archived tools unless `?status=` names them,
`api/db/tool_client.py:88-97`); Alembic `downgrade b3c1d9e4f7a2` from the 1.51.0 api container; only after that succeeds,
`dograh-api` then `dograh-ui` back to `1.49.0`. If the downgrade fails for another reason, restore the step-1 dump (loses every call
after the backup).
