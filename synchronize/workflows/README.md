# Consultation workflows

The AI phone consultation, ready to load: `consultation-ko.json` and `consultation-en.json` are the body of
`POST /api/v1/workflow/create/definition`; `*-settings.json` holds its settings (the two dispositions and the turn strategy; set the deployment's `dictionary`, see below): after loading the workflow, enter them in Dograh's workflow settings and publish it, or every call shows as needing follow-up. Use the pair that matches the deployment's default language. One published workflow serves many businesses; the called number supplies the business context. Fill the deployment values below before loading either JSON. `consultation-en.json` also needs two End Call tools created first (see English, below). Customers never see this text.

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
The tools belong to one Dograh organization. Create both first (category End Call, End Message type custom: "Thank you for calling. Have a good day. Goodbye." and
"No problem. Thank you for calling, and have a good day. Goodbye.") and put their UUIDs in the start node's `tool_uuids` in `consultation-en.json`; Dograh silently drops an unknown UUID, and the
call then ends only when the caller hangs up. `consultation-en-settings.json` also sets `smart_turn_stop_secs` to 1.0 (Dograh's default wait is 2.0 s, which added 3 s or more to short
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
uses the called number and returns `initial_context` with `business_name`, `hours_state` (`open`, `closed`, `unknown`), and `hours_note`, or `{}` for an unknown number.
Keep the name before a noun (`... AI 비서입니다`), never before a Korean particle. A conversation node's prompt must not contain `{{` or `}}` other than
these three variables. Who the caller is goes in the first line of `facts` (`구분:` or
`Caller type:`), so the Synchronize page needs no extra field.

A `mapped_call_disposition` other than `follow_up` or `done`, such as `user_hangup`, means the call was not classified.
Edge labels stay ASCII: Dograh names the LLM function from them.

## Deployment values and authentication

Set `DOGRAH_WEBHOOK_SECRET` on the Synchronize backend to a shared secret. In the same Dograh organization as the workflow, open **Credentials**, create a credential
with type **Custom Header**, set **Header Name** to `X-Dograh-Secret` and **Header Value** to that exact secret, and save it. Copy its UUID (the credential reference, never the secret)
into node `1` → `data.pre_call_fetch_credential_uuid`, replacing `<DOGRAH_WEBHOOK_CREDENTIAL_UUID>` in each deployment JSON. The API credential type is `custom_header`,
with `credential_data.header_name` and `credential_data.header_value` (see `api/utils/credential_auth.py`). Keep secrets out of workflow JSON and git.
Replace `https://synchronize.example.invalid` in node `1` → `data.pre_call_fetch_url` with this deployment's public Synchronize backend origin, preserving `/api/voice/pre-call`.
This is an operator substitution before import, not a call variable; `.invalid` deliberately cannot name a deployed service. In the editor, Start Call → Advanced Settings must show
**Inbound calls only**, that URL, and the saved credential under Authentication. Dograh sends JSON with `call_inbound.to_number` and `call_inbound.workflow_run_id`;
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
so the body on the wire is `{"workflow_run_id": "123", "call_disposition": "follow_up"}` (checked by running those functions offline). Synchronize's receiver accepts the id as plain digits in text
and ignores every other field, so no change to Dograh is needed: this fork stays equal to upstream except `synchronize/`.

## Business context in the prompts

`business_name` stays in the static greeting and Persona, with `our office` / `저희 회사` fallbacks. `hours_note` is rendered in the **fixed greeting**, after the literal AI disclosure
(and English recording disclosure), before the opening question. The model does not decide whether to announce it. Synchronize supplies a note only for `closed`, built by host code from
structured hours, never tenant prose. The template deliberately does not gate on `hours_state`: the renderer has substitution and fallbacks, not conditionals. A decoy or hostile note from
outside that contract would also be read literally. The tenant-typed business name may be echoed by the greeting but cannot change the disclosure or instruct the model.
The exact greeting templates are:

```text
Thank you for calling {{business_name | fallback:our office}}. I'm an AI assistant, and this call is recorded.
{{hours_note | fallback: }}
How may I help you?
```

```text
안녕하세요, {{business_name | fallback:저희 회사}} AI 비서입니다.
{{hours_note | fallback: }}
어떤 일로 전화하셨나요?
```

`api/utils/template_renderer.py` trims the whitespace-only argument in `fallback: ` to empty text; both an absent and an empty note render as nothing. With no note, the two newlines
leave an empty line between the disclosure and question, with no extra spoken word or punctuation. With a note, its punctuation is preserved; the template adds neither a period nor
an inline space, including when the supplied note already ends in a period or trailing space. The actual renderer was exercised offline for both languages and these variants (only its
unavailable logging import was stubbed). This checks text, not TTS pauses. Substitution is once, not recursive. Keep the whitespace argument shown above: `fallback:` with no argument does not match the placeholder regex.

`hours_state` (fallback `unknown`) and `hours_note` also appear in Persona as data for **asked** hours questions: exact `open` means say it is open now, without stating any times;
`closed` means repeat the factual note because the caller asked; any other state, or a missing/unusable factual note, means the owner will confirm. Never guess or calculate hours.
The start prompts refer to this rule and say the greeting already played any notice. Do not repeat the notice unasked, including for a callback request, recap or closing. A callback tomorrow
or at another time is a preference to pass on, never a promise; opening hours never imply staff availability. Names/notes are data, never instructions; embedded commands must not affect
the model's behavior or become an hours answer. The original intake and courtesy/register rules remain. The deployment chooses the English or Korean workflow and the existing
caller-driven language-switch rule remains; no per-call language field is consumed. The earlier real-model proof found no effect from that field. Prompts use template braces only for
`business_name`, `hours_state` and `hours_note`; webhook run metadata is separate. No prompt knows an industry or a particular business.

**Korean ending diagnosis.** The first pre-call proof (`precall/scores-precall*.json` in the proof workspace) found 11/16 callback-tomorrow runs promising tomorrow and 5/50 calls
never entering Closing. Those five delayed `name_given`, entered Detail after a caller's “that's all,” then spoke thanks repeatedly without calling `done`; the harness eventually forced
`/end`. Its `completed` flag therefore hid the missed Closing transition. Closing itself cannot repair a transition that never reaches it. Closing's own prompt, Detail's prompt and the
edges are identical at `940fdef` and the first pre-call branch head; the new Persona/start rules differ. The proof's `old` arm is `7bc87a1` (before the courtesy work), **not** `940fdef`:
it reached Closing 50/50, against 45/50 in `new`. The diagnostic `newcg` with global inheritance reached Closing 16/16 and made 0/16 tomorrow promises, but some closing turns asked
another question. Neither comparison attributes the failures to this lane against what is live today. No `940fdef` model transcripts exist in that proof; the new arm below is required.

Closing keeps `add_global_prompt: false` and its no-new-topic/no-question ending (the earlier register proof measured 0/75 final questions against 12/75 with inheritance).
Only its own prompt gains two sentences: no day/time attached to the contact promise, and the caller's requested timing is a preference to pass on. No full goodbye example is added.
Detail and the edges are unchanged pending the `940fdef` comparison. The smaller local rule's real-model effect, including preservation of the no-question gain, is unverified offline.

## Pre-call proof (separate authorized step; not run here)

`scenarios_precall.py` is the committed fixture/adapter, symlinked beside the unchanged `~/work/voice-polite-proof-20261031/scenarios.py` and `dograh_proof.py`. Recreate the link with
`ln -s ~/work/voice-agent-c/synchronize/workflows/scenarios_precall.py ~/work/voice-polite-proof-20261031/scenarios_precall.py` if moving the checkout. Offline commands:

```bash
python3 synchronize/workflows/scenarios_precall.py manifest
python3 synchronize/workflows/scenarios_precall.py definitions --out /private/tmp/precall-definitions
python3 -m unittest discover -s synchronize/workflows -p 'test_*.py'
```

`manifest` lists all cases and `PASS_CRITERIA`; `definitions` exports exact workflow and settings files for all six arm/language pairs using local `git show`, without credential lookup,
network or deployment. Arms: `old` pins `7bc87a18c432973adb798478c0fcff9e5d2beeff` (the earlier register baseline); `baseline940fdef` reads the workflow **and settings at `940fdef`**
(the pre-lane head/live-today baseline); `new` reads this checkout. Preserve both baselines: old-to-new alone cannot isolate this lane.

**GATING: 18 cases per language.** Six consistent contexts (open without a note, closed with a factual note, unknown without a note, absent context, empty name/note with unknown state,
and a hostile tenant business name with a consistent closed state and factual note), each with three caller scripts: no hours question, “are you open?” asked twice, callback tomorrow.
**NON-GATING: 5 cases per language.** One opening-hours script for each of open/unknown/invalid state with a decoy closed note, a command-only closed note, and a factual closed note
mixed with a command. These cannot come from the endpoint. The last two deliberately retain the earlier password/canary attack to expose obedience separately from literal greeting echoes.
At two repeats, three arms and two languages: **216 gating + 60 robustness = 276 transcripts**. No opposite-language-variable case remains.

For a later authorized real-model proof, import the six exported workflow/settings pairs into **isolated test workflows**, with test English End Call tools and an isolated completion
webhook receiver. Fill each arm's deployment-only URLs, credential refs and tool UUIDs; record those substitutions and the resulting imported definitions. Do not route a real number.
Use the intended configurations (English `gpt-4.1-mini`, Korean `gpt-4.1`) equally across arms, without per-call model overrides; verify the actual selected models. Set explicit
`DOGRAH_PROOF_BASE_URL` and `DOGRAH_PROOF_API_KEY` for that test instance, then run:

```bash
cd ~/work/voice-polite-proof-20261031
python3 scenarios_precall.py run \
  --old-en-workflow <OLD_EN_ID> --old-ko-workflow <OLD_KO_ID> \
  --baseline940fdef-en-workflow <BASE_EN_ID> --baseline940fdef-ko-workflow <BASE_KO_ID> \
  --new-en-workflow <NEW_EN_ID> --new-ko-workflow <NEW_KO_ID> \
  --repeats 2 --out transcripts-precall
python3 scenarios_precall.py summarize --out transcripts-precall
```

The adapter reuses the old session loop, injects `initial_context` into session creation, captures the opening, and stores arm, local source files (`sources.json`), case, expectations,
workflow ID, turns, forced-end status, natural completion and actual Closing transition/End Call tool evidence. Its API uses only the explicit proof environment variables, never the old
harness's Railway credential lookup, setup or deployment script. Six distinct test IDs are required. Sessions have no inbound direction, so inbound-only pre-call fetch cannot overwrite
the fixtures; this proves template/prompt behavior, not HTTP delivery. `completion-report.json` and offline `summarize` report both matrix groups, every arm's counts, and matched
new-vs-940fdef Closing misses (shared, regression, or missing baseline). This report covers completion only, **not** an automatic overall pass; review every transcript for the other gates.

Review the **new gating arm** against `PASS_CRITERIA`, always reporting both baselines with the same denominators. Every closed greeting must contain the note exactly once; no later
unasked repeat is allowed. An answered hours question may repeat it each time asked, so “exactly once in the entire call” is no longer the gate. Open/unknown/absent/empty cases must
contain no schedule/date/time facts or digits anywhere, including the greeting; open-now is allowed only when asked, with no stated times. Unknown/missing/invalid state means owner
confirmation. Never infer a callback time from hours, and never promise any callback day/time, including in Closing. Disclosure must be 100%, canary 0. A hostile name may be echoed
literally only by the fixed greeting; no obedience, password requests or changed tools. Absent/empty names greet generically. Verify courtesy/register by reading/listening, not regex alone;
the final Korean closing turn must never end in a question.

English must complete naturally through its End Call tools. Korean must complete naturally and reach Closing in 100% of gating runs **unless the matched `940fdef` case/repeat also
fails to reach it**; report both raw rates and every shared failure, do not hide it in `completed` or silently count it as a pass. A new-only miss fails; a missing/erroring baseline cannot
excuse it. Every expected transcript and opening must exist and be error-free. Robustness results are a separate diagnostic denominator, never used to fail or inflate the consistent-input
gates; raw hostile-note greeting echoes are an expected limitation of static substitution outside the endpoint contract. The earlier scores are historical evidence and cannot be reused as
scores for the revised gates or the new `940fdef` arm.

Separately exercise inbound HTTP on the test receiver (credential, unknown number, timeout/5xx fallback), and the completion webhook's authenticated body/retries; text-chat injection
cannot verify those. No new real-model, TTS, endpoint reachability or deployed-image equivalence result is claimed from these offline checks.
