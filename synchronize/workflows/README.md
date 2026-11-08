# Consultation workflows

The AI phone consultation, ready to load: `consultation-ko.json` and `consultation-en.json` are the body of
`POST /api/v1/workflow/create/definition`; `*-settings.json` holds its settings (the eight call outcomes and the turn strategy; set the deployment's `dictionary`, see below): after loading the workflow, enter them in Dograh's workflow settings and publish it, or no call is classified. Use the pair that matches the deployment's default language. One published workflow serves many businesses; the called number supplies the business context. Fill the deployment values below before loading either JSON. Both need the caller-work HTTP tool (`tool-lookup-caller-work-*.json`, see Caller memory below) and `consultation-en.json` also needs two End Call tools created first (see English, below). Customers never see this text.

Korean is three steps, the shape Dograh's own guide gives (one phase per node, a specific edge condition per exit). The start node plays the greeting, takes the
reason with a short "네,", and asks the name. The Detail node asks one open question that continues from what the caller said (a bare "anything else?" ended
new inquiries with no detail in real calls). The end node closes. Each exit depends only on what the caller says (the pattern of pipecat-flows and LiveKit's
AgentTask). The global node holds persona, tone and rules, and leaves no slot for an acknowledgement. Every change of step costs a second model call (0.45-0.93 s on
real calls: one call decides to move, one speaks), and a spoken filler at the change only moves the answer later, so a step has to earn its place. This one does: with the
question and the close in one start node, gpt-4.1 asked the question and handed over in the same turn in 5 to 8 of 40 bot turns (text-chat runs, 15 call types), so the caller heard a
goodbye with no room to answer. A prompt sentence, a more descriptive edge name, Dograh's "a turn is speech or the move, never both" and a skip clause did not remove it
(the clause cut it to 1 or 2 of 40); the separate node did (0 of 117). The earlier wording (an acknowledgement slot, an open "ask what the owner needs" step) caused the
restating, not the model (`../README.md`, Measured); with one open question the model still sometimes asks two or three, so real calls decide. Every node extracts what it has so
far, because a caller can hang up in any of them. A post-call QA node reviews every call. The prompts follow OpenAI's voice prompting guide (role, tone and length,
language, unclear audio, what never to do, hand-off).

Courtesy is one rule in the global node, written as behaviours and not as an adjective (LiveKit's guide: the model already tries to be friendly, so name the audible
behaviour). Korean: 합니다체 for greeting, thanks, apology and promises (`감사합니다`, `~드리겠습니다`), the honorific 해요체 for questions and requests, the caller's name followed by
`님` (not `씨`, never the bare name), and `죄송하지만` / `실례지만` at the front of the same sentence when the agent cannot answer, cannot connect or asks again. English: requests asked as
requests, "I'm sorry" at the same moments, and no thanks after answers, because the greeting and the fixed goodbye already thank. No OSS voice-agent project or vendor guide
(Dograh, LiveKit, Pipecat, OpenAI, Retell, ElevenLabs, Vapi, Hume) specifies Korean speech levels or honorifics, so the Korean split is a judgment: 국립국어원 sets no mandatory level, glosses
하십시오체 with greetings and farewells, and prefers `-시-` in requests; its phone-closing standard is `고맙습니다` or `안녕히 계십시오`. Dograh's own template (imperfect speech, fillers) and LiveKit's frontdesk
(mirror the caller's formality) are for other roles and are not copied. The Closing node does not inherit the global node (`add_global_prompt` off) and the register examples are endings, not
sentences: with the global node on, the closing turn asked the caller a question and the call ended in 12 of 75 scripted calls (28 with the courtesy rule added), and a full closing sentence
used as an example made the Detail step close early when a caller declined to give a name. A `DISCOURTEOUS` tag in the QA node reviews calls over 15 s.
Measured through the text-chat API, same caller lines for both versions. Korean, on the Korean line's configuration (`gpt-4.1`, 75 calls each): the closing turn ended on a question in 0 of 75 (was 12);
calls thanking more than once 20% (was 47%); cushion or apology wording 0.67 times per call (was 0.16); bot characters per call 191 against 182; blinded pairwise judges (Codex and Grok, positions balanced,
canaries passed) preferred the new version 38-7 and 40-5. English was first measured on `gpt-4.1` (22 calls each; judges 20-2 and 22-0), which is NOT the live line: the Twilio number runs `gpt-4.1-mini` with
ElevenLabs Flash v2.5 (voice Sarah). On that configuration (33 calls each): thanks after answers 0.94 to 0.09 per call, turns over two sentences 2 of 101 to 0 of 103, apology wording 0.09 to 1.00 per call, characters per call 395 to 446;
judges preferred the new version 25-8 (Codex) and 32-1 (Grok). Known and not fixed: a Korean caller who declines to give a name is sometimes pressed for it, `성함이 ○○님이시군요` echoes the caller, and a question plus the goodbye can still land in one turn;
on `gpt-4.1-mini` the English agent restates the caller a little more (judges flagged it in 15 and 24 of 33 calls, against 8 and 20) and sometimes says "I'm sorry" when answering "are you an AI?" (4 of 33). Not measured: how any of it sounds over 8 kHz TTS, and turn-taking.

English is one agent, not three steps, because every change of step costs a second model call (0.45-0.93 s on real calls). The start node
`Reception` holds the whole conversation (reason, name, one open question, a short recap, "anything to add?") and the call ends through one of
two End Call tools, `end_call` and `end_call_wrong_number`, instead of an end node: an End Call tool plays a fixed goodbye and hangs up with no further model call.
The tools belong to one Dograh organization. Create both first (category End Call, one start message under "When the tool starts", Dograh 1.51: "Thank you for calling. Have a good day. Goodbye." and
"No problem. Thank you for calling, and have a good day. Goodbye.") and put their UUIDs in the start node's `tool_uuids` in `consultation-en.json`; Dograh silently drops an unknown UUID, and the
call then ends only when the caller hangs up. The live tools' 1.49 "End Message type custom" goodbyes become that start message in the 1.51 migration (`../UPGRADE-1.51.md`). `consultation-en-settings.json` also sets `smart_turn_stop_secs` to 1.0 (Dograh's default wait is 2.0 s, which added 3 s or more to short
answers). The `facts` and `quotes` extraction prompts say plain text because the extraction model sometimes returned a JSON list; the Synchronize page joins a list into
lines as a safeguard. Real calls of this shape are not measured yet.

Speech recognition mishears the business name unless it is given as a keyterm: on a recorded call Deepgram heard `영혼솔루션` without it and
`영훈솔루션` with it. Dograh reads the workflow field `dictionary` (comma-separated, once per workflow, no call variables), so set it to the
comma-separated names of the businesses served by this deployment on the shared workflow; update it when adding a business. It cannot vary per call.

Both are an office front desk, not only a consultation line: the caller may be a new inquiry, an existing customer, a partner or
vendor, or a sales call. The greeting says it is an AI (Korea AI Framework Act Art. 31 and MSIT guideline, for a voice that can pass as human); the English
one also says the call is recorded (all-party-consent US states; a Korean recording notice is not required by 통신비밀보호법, only by PIPA if
the legal basis is consent; the customer's privacy policy must name the processors and the cross-border transfer). It takes the message, and promises nothing
about price, schedule, feasibility or the status of existing work. `consultation-ko.json` and `consultation-en.json` are the same product prompt in two languages; neither names a business or an
industry. The business name is the variable `{{business_name | fallback:...}}` in the greeting and the global prompt (verified: it renders
in both, and the fallback fills in when the value is missing). Dograh gives a production call no such value by itself (workflow-level
context variables apply to test calls only). Both start nodes now use inbound-only Pre-Call Data Fetch: Synchronize's `POST /api/voice/pre-call`
uses the called number and returns `initial_context` with `business_name`, or `{}` for an unknown number.
Keep the name before a noun (`... AI 비서입니다`), never before a Korean particle. A conversation node's prompt must not contain `{{` or `}}` other than
the call variables `business_name`, `caller_name` and `caller_history`, each with a non-empty fallback: Dograh renders every `{{word}}` in a prompt from the
call's context, a variable it does not have becomes an empty string, and `{{x | fallback:}}` with nothing after the colon stays in the prompt as literal braces
(`../UPGRADE-1.51.md`, Fields). Who the caller is goes in the first line of `facts` (`구분:` or
`Caller type:`), so the Synchronize page needs no extra field.

A `mapped_call_disposition` that is not one of the eight outcome codes, such as `user_hangup`, means the call was not classified: Dograh 1.51 stamps the call
status at hangup and the post-call classifier replaces it once it finishes after the hangup, or never when it fails. Synchronize treats it as needing follow-up.
Edge labels stay ASCII: Dograh names the LLM function from them.

## Caller memory, record lookup and outcomes

Synchronize's side (branch `fm/261108_voice_caller_context`) gives the pre-call fetch two more `initial_context` keys, both plain text and omitted when there is
nothing to say: `caller_name` (exactly one contact of the called company has the calling number and a real name) and `caller_history` (at most three short Korean
lines: open matters with their due day, the site, the last contact date). The Persona lists them under `전화한 분` / `Caller` as data, with the fallbacks `모름` / `없음`
and `unknown` / `none`, so an unknown caller gets today's prompts. The greeting does not change: Dograh's renderer has no conditional text, so a name in the
greeting would leave a broken sentence for an unknown caller, and a pre-call `greeting_override` is read before the fetch is merged (`../UPGRADE-1.51.md`). A known
caller is asked to confirm that name instead of giving one (Korean edge `name_given` counts the confirmation), the Detail step can refer to the open matter, and the
Persona shares what a record says only with the person it names once they confirmed it.

During the call the agent can look the caller up: `tool-lookup-caller-work-ko.json` / `-en.json` hold the Dograh HTTP API tool (`POST /api/v1/tools/` body under `tool`,
written with every field Dograh stores so the read-back is exact). It posts `{"workflow_run_id": "<digits>"}` from the preset `{{initial_context.workflow_run_id}}`
(run-owned in Dograh, the caller cannot set it; the model supplies nothing) to Synchronize's `/api/voice/tools/caller-work` with the same `X-Dograh-Secret` Custom
Header credential, times out after 5000 ms, says `확인해 보겠습니다.` / "Let me check." when it starts and `잠시만 기다려 주세요.` / "One moment, please." if the answer takes
over 2.5 s. Synchronize answers facts only (`known`, `name`, `open_work`, `upcoming_visits`, `estimates` with status, no amounts or addresses). The tool is on the Korean
Detail node (after the name step, so nothing is told to an unconfirmed caller) and on the English Reception node. Tools are organization objects referenced by UUID: the
templates name it with the reserved placeholder `00000000-0000-4000-8000-000000000101` (ko) / `…0102` (en), which `golive.py` replaces with the real tool's UUID; Dograh
would silently drop the placeholder, and go-live refuses to publish it. The Persona's never-promise rule now allows repeating what the lookup returned and nothing more;
the QA tag `PROMISED_UNCONFIRMED` says the same. Added prompt text: the `전화한 분` / `Caller` section, the role line naming that record, the confirm-the-name sentence in the
start step, one Detail / Reception sentence on when to look up, and the QA sentence. None of it is measured yet.

The settings carry eight outcomes, each with a classifier description in the workflow's language: `quote_request` (new quote or price), `booking_request` (visit,
measurement or appointment, including one needed for a quote), `urgent` (damage or danger now; chosen before anything else that fits), `complaint`, `message` (anything
else the owner must check or return, including the status of existing work), `done` (nothing left for the owner), `spam` (sales, ads, robocalls) and `wrong_number`.
Synchronize shows the code as the call's label and treats every code except `done`, `spam` and `wrong_number`, and every unclassified call, as needing follow-up.
`scenarios_caller.py` measures these through the text-chat API (Evals, below).

## Deployment values and authentication

For a new import, substitute the values below. For existing customised live workflows, use **Live wiring without re-importing** below.

Set `DOGRAH_WEBHOOK_SECRET` on the Synchronize backend to a shared secret. In the same Dograh organization as the workflow, open **Credentials**, create a credential
with type **Custom Header**, set **Header Name** to `X-Dograh-Secret` and **Header Value** to that exact secret, and save it. Copy its UUID (the credential reference, never the secret)
into node `1` → `data.pre_call_fetch_credential_uuid`, replacing `<DOGRAH_WEBHOOK_CREDENTIAL_UUID>` in each deployment JSON. The API credential type is `custom_header`,
with `credential_data.header_name` and `credential_data.header_value` (see `api/utils/credential_auth.py`). Keep secrets out of workflow JSON and git.
Replace `https://synchronize.example.invalid` in node `1` → `data.pre_call_fetch_url` with this deployment's public Synchronize backend origin, preserving `/api/voice/pre-call`.
This is an operator substitution before import, not a call variable; `.invalid` deliberately cannot name a deployed service. In the editor, Start Call → Advanced Settings must show
**Inbound calls only**, that URL, and the saved credential under Authentication. Dograh sends JSON with `call_inbound.from_number`, `call_inbound.to_number` and `call_inbound.workflow_run_id`;
Synchronize returns variables only. The workflow does not request greeting or model overrides. If the request fails, times out, returns invalid JSON, or returns `{}`,
Dograh continues with earlier context and the generic greeting fallback (`docs/voice-agent/pre-call-data-fetch.mdx`).

Node `6` is the sole caller of `POST /api/voice/call-ended`: replace the same `.invalid` origin in `data.endpoint_url`, and put that same credential UUID in
`data.credential_uuid`. It is an enabled `POST` Webhook node, with no incoming or outgoing edges: Dograh runs integration nodes asynchronously after completion,
including caller hangups and English End Call tool exits, after QA. Do not connect it to the conversation graph or add a second tool/integration calling this endpoint.
Import the substituted workflow, apply its settings, check both Authentication selectors, and publish. Dograh's durable delivery worker retries transient failures
(including HTTP 500/502/503/504) with backoff, up to five total attempts by default (`WEBHOOK_DELIVERY_MAX_ATTEMPTS=5`, `api/constants.py`, `api/tasks/webhook_delivery.py`);
permanent auth/validation errors dead-letter immediately. The developer reference's old "no retry by default" sentence is stale against this checkout.

**What the webhook sends.** The template holds only `workflow_run_id`. `WebhookNodeData` in `api/services/workflow/dto.py` lists `workflow_run_id` as payload context, `docs/voice-agent/webhook.mdx` shows it, and
`_build_render_context` in `api/tasks/run_integrations.py` supplies `workflow_run.id`. The renderer turns the placeholder into text and `_build_webhook_payload` always adds `call_disposition`,
so the body on the wire is `{"workflow_run_id": "123", "call_disposition": "quote_request"}` (checked by running those functions offline at 1.49; `api/tasks/run_integrations.py` is unchanged at 1.51, which now sends it after the post-call classification). Synchronize's receiver accepts the id as plain digits in text
and ignores every other field, so no backend change to Dograh is needed. This lane changes `synchronize/` and ignores local session state in `.gitignore`; no `.omc/` files are tracked.

## Live wiring without re-importing

`python3 synchronize/workflows/golive.py [plan]` replaces the proof workspace's retired `deploy_live.py`.
It reads **local git objects only**: `--old-ref` defaults to `940fdef`, `--new-ref` to this checkout's `HEAD`.
Commit intended template changes before selecting `HEAD`. Target **3/en** or **1/ko** explicitly; **2, Korean realtime, is excluded**.
Of the settings template only `call_dispositions` is applied, and only its old-ref → new-ref delta: live equal to the old list is `template-replaced`, no list is
`set`, any other list is `REPLACES-CUSTOM` and needs `--accept-merged settings.call_dispositions`; every other setting and the model override stay as they are.
Stable node IDs/types preserve renamed nodes, custom nodes, edges, positions and tools. Template changes to node data and to edge data (`edge 1-2.data.condition`)
merge the same way; a template that adds, removes or re-targets an edge is refused before any write.

The caller-work tool (`tool-lookup-caller-work-{lang}.json` at the new ref) is found by its unique name among the organization's non-archived tools: absent → created,
different description/icon/definition → updated in place (name and category never change), equal → untouched. Its URL is the runtime origin + `/api/voice/tools/caller-work`
and its credential is the named `--credential-name` credential. A duplicate name, or a tool of that name that is not an active HTTP API tool, aborts before any write.
The template's placeholder UUID resolves to the tool's UUID; the published definition and the tool are both read back.

Text deltas use exact-once line/character merges. Insert/delete hunks use up to two unchanged context lines on each side;
a new fragment elsewhere is insufficient evidence that a change was already applied. A single-line greeting becoming multiline uses an anchored character delta,
so the Korean `영훈솔루션` greeting keeps its business name while adopting the template's line break. Character merging is allowed only for single-line-to-multiline
expansions with exact-once literal anchors on both sides (at least eight non-padding characters each, at word boundaries, through the field end).
Missing multiline hunk lines abort without a character fallback;
ambiguous or incompatible fields abort **before any remote write**, naming the field. LF/CRLF and NFC/NFD differences produce an explicit hint;
resolve those differences in the named field rather than silently rewriting its Unicode or line endings.
For the known Korean `node 4.data.prompt` conflict (`{{business_name}}` was customised), review the new template's field,
edit that field in the Dograh UI to the reviewed new-template value, **publish that reviewed edit**, then re-plan and apply.
This explicitly resolves the conflict; the tool never overwrites an incompatible customisation automatically.

Supply `--runtime-origin` (HTTPS origin, no path/query/credentials/placeholders), `--credential-name` and a **durable** `--backup-dir`
outside any git checkout, such as `~/golive-backups`. `/tmp`, `/private/tmp` and their descendants are refused.
HTTP loopback is allowed for offline fakes only. `DOGRAH_PROOF_BASE_URL` and `DOGRAH_PROOF_API_KEY` come from the environment;
quotes/newlines and redirects are refused, proxy environment settings ignored, connection values never printed.
The tool requires the fetched `version_status` to be `published` at backup and again immediately before applying.
**An unpublished draft must be reviewed and published, or discarded, in the Dograh UI first.** Fetch prefers a draft (`api/routes/workflow.py:814–837`),
and PUT keeps an existing draft's settings (`api/db/workflow_client.py:242–249`); an editor model autosave must never be published by this tool unnoticed.
Avoid concurrent editor changes during apply/rollback; the pre-write comparison includes definition, settings, status, version and definition ID,
but the API has no conditional PUT to close the final read/write race.

Both plan and apply flush a timestamped `0600` format-version-3 backup containing the definition, settings, template variables, version number, published definition ID
and the caller-work tool as it was (or `null` when it did not exist).
The version/ID and path are printed; apply explicitly prints **`ROLLBACK FILE: <path>`**. Keep that file for recovery.
Backups never contain the supplied secrets; workflow data containing either is refused. QA/provider secrets remain masked by Dograh's API.
Plan has no remote writes. Each changed template field prints
`node N.data.FIELD: template-replaced; -R/+A changed lines` when live equalled the old template, or
`node N.data.FIELD: MERGED-INTO-CUSTOM; -R/+A changed lines` when a custom field was merged.
Custom merges include a zero-context diff of changed text lines for review; unchanged prompt lines are withheld.
Other template fields with no actual change are omitted. Operator wiring fields use `wiring` in the same format;
rollback fields use `restore-backup`. URL paths omit origins; secrets are never shown.
`settings.call_dispositions: MODE; codes` prints the old and new code lists (descriptions withheld) and `tool NAME: create|update FIELDS|unchanged` the tool change.
The all-zero UUID denotes a credential that does not yet exist, and the `00000000-0000-4000-8000-` family the template's tool placeholders; none is ever published.

`--apply` requires `DOGRAH_WEBHOOK_SECRET` from the environment only. It creates or rotates the uniquely named `custom_header` credential,
using `header_name = X-Dograh-Secret`. Use a dedicated shared credential: rotation affects every consumer.
Before any remote write, apply refuses every `MERGED-INTO-CUSTOM` field unless explicitly named with repeatable
`--accept-merged FIELD`. After reviewing the plan's diff, use e.g.
`golive --apply --accept-merged 'node 1.data.greeting' --accept-merged 'node 4.data.prompt'`.
Accept only the exact fields printed as merged; the flag never bypasses an ambiguous/conflicting merge or the published/draft guards.
If the planned definition and dispositions already equal live, the tool is unchanged and the named credential exists, it says **Already wired** and skips rotation, PUT and publish;
it cannot compare a masked stored secret, so changing the environment secret alone does not rotate an already-wired workflow.
Writes run in this order: credential create/rotate, tool create/update and tool read-back, wiring validation, draft save (definition, plus settings only when the
dispositions change), publish, read-back. A tool-only change saves no draft.
Start node 1 gets the exact `inbound` mode and `/api/voice/pre-call` URL/authentication. Webhook node 6 is copied from the template if missing;
otherwise only its URL/authentication change. It must be POST, enabled (omitted `enabled` defaults to true per `api/services/workflow/dto.py:837`) and disconnected from graph edges.
PUT saves a draft; publish releases it; read-back must equal the entire plan, preserve every existing node and all settings/model overrides except the planned
dispositions, and contain no wiring or tool placeholders.

Every write/read-back stage is labelled, including wiring validation after credential creation/rotation. A failure after a write attempt prints one recovery block with the failed stage, credential state,
tool state, published-definition state and draft state. When the workflow may have changed, it includes the exact rollback command.
A rejected draft save (4xx), or validation failure before that save, says no workflow rollback is needed; any credential or tool change remains.
Malformed API responses fail with a suppressed-detail error and recovery evidence when a write was attempted.
Transport failures or HTTP 5xx mean **unknown, may have committed**.
If PUT succeeded and publish failed, anyone pressing **Publish** in Dograh would ship the wiring: roll back or discard that draft before using the editor.
Credential/workflow writes are separate transactions. Rollback retains the credential and its current secret; restore an older shared secret separately if needed.

`--rollback /absolute/path/to/backup.json` uses the saved workflow ID, backs up the current state, prints the field diff and warns that it **overwrites later edits**.
It can recover a draft left by a failed apply, saves/publishes the backup definition, and sends only settings that differ from the backup to avoid a lossy typed round trip.
A current draft's inspection backup is labelled draft and cannot itself be restored as a release; all backups except format versions 2 and 3 are refused with an explicit message. Rollback verifies equality against the fetched API representation;
masked/server-only values cannot be recovered from an API backup. Historical definitions need not have the new wiring.
A tool the backup recorded is restored to its backed-up description, icon and definition (then read back); a tool created after the backup is retained, like the
credential, and the restored definition no longer references it. Dograh's 1.51 → 1.49 migration downgrade refuses a tool with a delayed message (`../UPGRADE-1.51.md`).

`probe --workflow-id 3 --runtime-origin "$RUNTIME_ORIGIN" --to-number "$TO_NUMBER"` sends requests only to the runtime; it makes no direct Dograh requests.
It mirrors `api/services/pipecat/pre_call_fetch.py:109–123`: `event = call_inbound`, integer `agent_id` from `--workflow-id`, synthetic caller `+12025550100`,
called E.164 number, integer `workflow_run_id = -2147483647`, and `Idempotency-Key: dograh-pre-call--2147483647`.
The Synchronize runtime source (`src/synchronize/web/voice_api.py` in the main repo) ignores pre-call agent/run IDs and uses only the called number;
the negative pre-call ID deliberately cannot name a real Dograh run. Probe expects 200 JSON with the environment secret and 401/403 with a wrong secret.
It also probes `/api/voice/call-ended` with `{"workflow_run_id":"2147483647"}` and the same correct/wrong secret headers.
This is the largest value Dograh's 32-bit `workflow_runs.id` column can hold, so the lookup answers a clean 404 instead of a database range error (an 18-digit id would make Dograh answer 500 and the probe fail on a correct deployment). `"0"` fails validation (the route still answers 200 `{}`),
so it would not exercise the lookup path. For the deliberately unknown positive ID, `Voice.owning_tenant` (`src/synchronize/voice.py`)
maps Dograh's missing-run 404 to `None`; the completion route returns **200 `{}` without enqueueing a notice**. A lookup failure instead returns 503 and fails the probe.
It then posts the same body to `/api/voice/tools/caller-work`, as the HTTP tool sends it, and expects 200 `{"known": false}` (an unknown run) and 401/403 with the wrong secret.
Successful probe output is six status-only lines in order: pre-call correct secret `200`, pre-call wrong secret `401`/`403`,
call-ended correct secret `200`, call-ended wrong secret `401`/`403`, caller-work correct secret `200`, caller-work wrong secret `401`/`403`. A 404, 422 or 5xx at any route fails,
so a runtime without the caller-work route stops the go-live before anything is wired.
An older runtime that silently drops digit-text ids also answers 200 `{}`, so this probe cannot tell the two receivers apart: only the test call proves that completions are processed.
The runtime's completion lookup reads Dograh; it does not create a run. The deployed runtime's behavior and absence of that synthetic ID cannot be established offline.
The test call must still verify business context, a saved call and authenticated completion separately.

Operator setup in the owner's terminal: set the nonsecret `DOGRAH_PROOF_BASE_URL`, `RUNTIME_ORIGIN`, `CREDENTIAL_NAME`, called `TO_NUMBER`,
`WORKFLOW_ID=3 LANGUAGE=en`, and `BACKUP_DIR="$HOME/golive-backups"`. Export `DOGRAH_PROOF_BASE_URL`.
For Korean, repeat probe/plan/apply with `WORKFLOW_ID=1 LANGUAGE=ko` and the correct called number; English workflow 3 owns the only Twilio number today.
Use the absolute path in the function so Railway commands can run from the production project's link directory:

```bash
cd /Users/enoch/work/deploy-20261029/railway-link
golive() { python3 /Users/enoch/work/voice-agent-c/synchronize/workflows/golive.py "$@" --workflow-id "$WORKFLOW_ID" --lang "$LANGUAGE" --runtime-origin "$RUNTIME_ORIGIN" --credential-name "$CREDENTIAL_NAME" --backup-dir "$BACKUP_DIR"; }
IFS= read -rs DOGRAH_PROOF_API_KEY; export DOGRAH_PROOF_API_KEY; printf '\n'
```

Run in this order. Paste secrets only at silent reads. Step 2 must finish successfully and the runtime must be healthy before step 3;
`--skip-deploys` alone does **not** activate the secret. Pre-call auth errors fall back to empty context (`api/services/pipecat/pre_call_fetch.py:204–214`),
and completion auth errors dead-letter (`api/tasks/webhook_delivery.py:327–330`), so applying Dograh wiring before runtime activation can lose completions.
The stdin form below matches `set-resend-env.sh` and `set-soniox-railway-vars.sh`; installed offline CLI 5.49.6 help also confirms it.
CLI 5.71.0 and production deployment behavior were not verified offline.

```bash
# 1. Stage the secret on the explicit production runtime service.
IFS= read -rs DOGRAH_WEBHOOK_SECRET; export DOGRAH_WEBHOOK_SECRET; printf '\n'; printf '%s' "$DOGRAH_WEBHOOK_SECRET" | railway variable set DOGRAH_WEBHOOK_SECRET --stdin --service synchronize-runtime --environment production --skip-deploys >/dev/null
# 2. Redeploy the runtime; wait for success/healthy status before continuing.
railway redeploy --service synchronize-runtime --environment production
# 3–5. Probe runtime auth first, then review the plan, then apply.
golive probe --to-number "$TO_NUMBER"
golive plan
golive --apply # Add --accept-merged 'node N.data.FIELD' for EACH reviewed custom merge.
# 6. Place a test call; check greeting/business context, the saved call and authenticated completion.
# 7. Recovery only: set BACKUP_FILE to the apply run's ROLLBACK FILE path, then:
# golive --rollback "$BACKUP_FILE"
unset DOGRAH_WEBHOOK_SECRET DOGRAH_PROOF_API_KEY
```

For later recovery, securely re-read/export `DOGRAH_PROOF_API_KEY` before `golive --rollback "$BACKUP_FILE"`, then unset it again.

Offline targeted proof: `python3 synchronize/workflows/test_golive.py` (standard-library unittest, loopback fake server; no pytest or external calls).
It covers both languages, draft refusal at both reads, private versioned backups, Korean greeting/conflict handling, silent-skip regressions,
create/rotate/no-op, every failure stage and uncertain writes, sanitizer/QA-mask fidelity, rollback with a pending draft/settings diffs, Unicode hints, durable paths and exact probe payload/auth,
tool create/update/unchanged/ambiguous/inactive, tool restore on rollback, format-2 backups, dispositions-only settings (replaced, set, custom needs acceptance), edge merges and conflicts,
placeholder refusal and the caller-work probe. When `pydantic` is importable it also validates the committed tool and settings files against this checkout's
Dograh schemas (`api/schemas/tool.py`, `api/schemas/workflow_configurations.py`, identical to the `dograh-v1.51.0` tag).
No live reachability, deployed image/API equivalence, runtime redeployment, call audio or authenticated real completion is established offline.

## Business context in the prompts

`business_name` stays in the static greeting and Persona, with `our office` / `저희 회사` fallbacks. The value is data, never instructions, and the backend collapses whitespace, applies the length cap and omits an empty name. Dograh's renderer does not trim tenant values, so that normalization remains the backend contract.

The exact greeting templates are:

```text
Thank you for calling {{business_name | fallback:our office}}. I'm an AI assistant, and this call is recorded.
How may I help you?
```

```text
안녕하세요, {{business_name | fallback:저희 회사}} AI 비서입니다.
어떤 일로 전화하셨나요?
```

The deployment chooses the English or Korean workflow, and the caller-driven language-switch rule remains. No per-call language field is consumed. Webhook run metadata is separate.

## Pre-call proof (separate authorized step; not run here)

`scenarios_precall.py` is the committed fixture/adapter for the still-live name, disclosure, callback-preference and completion behavior. Its matrix covers named, absent, empty and hostile names with an ordinary message and a callback-tomorrow request in both languages. Offline commands:

```bash
python3 synchronize/workflows/scenarios_precall.py manifest
python3 synchronize/workflows/scenarios_precall.py definitions --out /private/tmp/precall-definitions
python3 -m unittest discover -s synchronize/workflows -p 'test_*.py'
```

`manifest` lists every case and the pass criteria. `definitions` exports exact workflow and settings files for all six arm/language pairs using local `git show`, without credential lookup, network access or deployment. Arms: `old` pins `7bc87a18c432973adb798478c0fcff9e5d2beeff`; `polite` reads the workflow and settings at `940fdef`; `new` reads this checkout.

For any later authorized model proof, use isolated test workflows and explicit `DOGRAH_PROOF_BASE_URL` / `DOGRAH_PROOF_API_KEY`. Do not route a real number. The adapter injects `initial_context`, captures the opening, and records natural completion plus Korean Closing or English End Call tool evidence.

## Evals: caller memory, lookup and outcomes (owner-run; not run here)

`scenarios_caller.py` runs eight cases per language through the text-chat API: a known caller greeted by name, a known caller asking `견적 어떻게 됐어요?` /
"How's my quote coming along?" (the lookup tool is called, the result is relayed without promises), the same question from an unknown caller (today's greeting),
and the outcome of a quote request, an urgent leak, a complaint, a sales call and a wrong number. `manifest` (offline) prints every case and its criteria.
`run` builds one isolated test workflow from this checkout's committed templates (`HEAD`) with the live lookup tool, the live line's model configuration and the
template settings; it removes the pre-call fetch, the completion webhook and the QA node, archives the test workflow at the end, and never touches the live workflow
beyond reading its model configuration. It scores the objective checks (opening text, the name in the first reply, the tool call, the outcome code) and writes every
transcript with a review block for the judged criteria (no promise, no re-asked name). `cleanup --out DIR` archives what a crashed run left (`DIR/state.json`).
A text-chat run is not an inbound call, so Synchronize's caller-work route answers `{"known": false}` to it: the lookup case measures that the tool is called and
that the agent relays "nothing found, the owner will check" without promising; relaying real records is checked on the second test call of the runbook.
Text chat also does not recover tool calls the model wrote as text (`../UPGRADE-1.51.md`). The one command (after golive `--apply`, with the two connection values
exported):

```bash
python3 synchronize/workflows/scenarios_caller.py run --lang ko --live-workflow-id 1 --repeats 2 --out "$HOME/voice-evals/caller-ko-$(date -u +%Y%m%dT%H%M%SZ)"
```

Offline proof of the runner: `python3 synchronize/workflows/test_scenarios_caller.py` (loopback fake text-chat API).
