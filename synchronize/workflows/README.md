# Consultation workflows

The AI phone consultation, ready to load: `consultation-ko.json` and `consultation-en.json` are the body of
`POST /api/v1/workflow/create/definition`; `*-settings.json` holds its settings (the six call outcomes and the turn strategy, and on English the call limit and the model services, see Model, speech recognition and call limit below; set the deployment's `dictionary`, see below): after loading the workflow, enter them in Dograh's workflow settings and publish it, or no call is classified. Use the pair that matches the deployment's default language. One published workflow serves many businesses; the called number supplies the business context. Fill the deployment values below before loading either JSON. Both need the caller-work HTTP tool (`tool-lookup-caller-work-*.json`, see Caller memory below) and `consultation-en.json` also needs two End Call tools created first (see English, below). Customers never see this text.

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
`Reception` holds the whole conversation (reason, name, what that kind of call needs, a short recap, "anything to add?") and the call ends through one of
two End Call tools, `end_call` and `end_call_wrong_number`, instead of an end node: an End Call tool plays a fixed goodbye and hangs up with no further model call.
The tools belong to one Dograh organization. Create both first (category End Call, one start message under "When the tool starts", Dograh 1.51: "Thank you for calling. Have a good day. Goodbye." and
"No problem. Thank you for calling, and have a good day. Goodbye.") and put their UUIDs in the start node's `tool_uuids` in `consultation-en.json`; Dograh silently drops an unknown UUID, and the
call then ends only when the caller hangs up. The live tools' 1.49 "End Message type custom" goodbyes become that start message in the 1.51 migration (`../UPGRADE-1.51.md`). `consultation-en-settings.json` also sets `smart_turn_stop_secs` to 1.0 (Dograh's default wait is 2.0 s, which added 3 s or more to short
answers), and so does `consultation-ko-settings.json` since A2: the name confirmation (`○○님 맞으세요?` → `네`) makes short Korean answers
common. Go-live applies the outcomes, the call limit and the model services from the settings file but not the turn settings, so the runbook (step 5) sets the wait in Dograh. The `facts` and `quotes` extraction prompts say plain text because the extraction model sometimes returned a JSON list; the Synchronize page joins a list into
lines as a safeguard. Real calls of this shape are not measured yet.

Speech recognition mishears the business name unless it is given as a keyterm: on a recorded call Deepgram heard `영혼솔루션` without it and
`영훈솔루션` with it. Dograh reads the workflow field `dictionary` (comma-separated, once per workflow, no call variables), so set it to the
comma-separated names of the businesses served by this deployment on the shared workflow; update it when adding a business. It cannot vary per call.

Both are an office front desk, not only a consultation line: the caller may be a new inquiry, an existing customer, a partner or
vendor, or a sales call. The greeting says it is an AI (Korea AI Framework Act Art. 31 and MSIT guideline, for a voice that can pass as human) and that the
call is recorded. English needs the recording notice in all-party-consent US states. Korean law does not require one (통신비밀보호법; PIPA only if the legal
basis is consent), but announcing it is the standard for Korean call answering (KT 통화매니저 does), so the Korean greeting says `통화 내용은 녹음되어
담당자에게 전달됩니다.` after the AI disclosure, in the greeting's 합니다체 (amendment A1 §5). The customer's privacy policy must name the processors and the
cross-border transfer. It takes the message, and promises nothing
about price, schedule, feasibility or the status of existing work. `consultation-ko.json` and `consultation-en.json` are the same product prompt in two languages; neither names a business or an
industry. The business name is the variable `{{business_name | fallback:...}}` in the greeting and the global prompt (verified: it renders
in both, and the fallback fills in when the value is missing). Dograh gives a production call no such value by itself (workflow-level
context variables apply to test calls only). Both start nodes now use inbound-only Pre-Call Data Fetch: Synchronize's `POST /api/voice/pre-call`
uses the called number and returns `initial_context` with `business_name` and the business facts and hours (Business facts and hours, below), or `{}` for an unknown number.
Keep the name before a noun (`... AI 비서입니다`), never before a Korean particle. A conversation node's prompt must not contain `{{` or `}}` other than
the call variables `business_name`, `caller_name`, `service_area`, `pricing_policy`, `open_now`, `hours_today` and `next_open` (and, on the English line only,
`urgent_examples`, and Dograh's own `current_time` in the extraction prompt), each with a non-empty fallback: Dograh renders every `{{word}}` in a prompt from the
call's context, a variable it does not have becomes an empty string, and `{{x | fallback:}}` with nothing after the colon stays in the prompt as literal braces
(`../UPGRADE-1.51.md`, Fields). Who the caller is goes in the first line of `facts` (`구분:` or
`Caller type:`), so the Synchronize page needs no extra field.

A `mapped_call_disposition` that is not one of the six outcome codes, such as `user_hangup`, means the call was not classified: Dograh 1.51 stamps the call
status at hangup and the post-call classifier replaces it once it finishes after the hangup, or never when it fails. Synchronize treats it as needing follow-up.
Edge labels stay ASCII: Dograh names the LLM function from them.

## Caller memory, record lookup and outcomes

Synchronize's side (branch `fm/261108_voice_caller_context`) gives the pre-call fetch one more `initial_context` key, `caller_name`, sent only when exactly
one contact of the called company has the calling number and a real name. Nothing else about the caller comes before the call: task objectives and site
names are free text that can carry amounts, names and private notes, and caller ID alone is not identity, so a known caller is greeted by name only
(amendment A1 §1). The Persona lists the name under `전화한 분` / `Caller` as data, with the fallback `모름` / `unknown`, so an unknown caller gets today's
prompts. The greeting does not change for a known caller: Dograh's renderer has no conditional text, so a name in the greeting would leave a broken sentence
for an unknown caller, and a pre-call `greeting_override` is read before the fetch is merged (`../UPGRADE-1.51.md`). A known caller is asked to confirm that
name instead of giving one (Korean edge `name_given` counts the confirmation), and `customer_name` takes a confirmed name as well as a spoken one (the
post-call extraction reads only the conversation, never `initial_context`, and Synchronize names the call by `customer_name`). The record rule has one
wording in both languages, `기록의 이름이 맞다고 확인한 본인` / a caller who `confirmed the name on the caller record`, and every place the agent reads about
the lookup names that same person: the Persona's exception under `하지 않는 것` / `What you never do` (what a lookup in this call shows may be told, as what the
record shows, only to that caller), and the tool description's when-to-use sentence (the lookup is for that caller asking when their next visit is) and relay clause (the
visit days may be told to that caller). A record with no name has no name to confirm, and Synchronize answers it `{"known": false}`, so
nothing is told. The agent never brings up a visit by itself; visits come only from the lookup.

During the call the agent can look the caller up: `tool-lookup-caller-work-ko.json` / `-en.json` hold the Dograh HTTP API tool (`POST /api/v1/tools/` body under
`tool`, written with every field Dograh stores so the read-back is exact). Its `body_template` is `{"workflow_run_id": "{{initial_context.workflow_run_id}}", "language": "<ko|en>"}` (the workflow's own language as a constant, so Synchronize speaks visit days in the call's language),
so the request body is that one key, filled from the run's own context, and nothing the model sends reaches it. A preset alone would not hold: at 1.51 the
model's arguments override presets and, with no body template, are the body (`api/services/workflow/tools/custom_tool.py:432`, `:473-474`), so a caller
could have the model name another call's run. A body template sends only its own keys and is rendered with the run's `initial_context` laid over the
arguments (`:476-482`); Dograh sets `workflow_run_id` there from the run row (`api/services/pipecat/run_pipeline.py:693`; text chat
`api/services/workflow/text_chat_runner.py:557-560`) and never takes it from an external context (`api/services/workflow/initial_context.py:10-21`). A value
that is one whole placeholder keeps its type (`custom_tool.py:278-295`), so the id arrives as a JSON integer; Synchronize accepts that or digit text. The
required preset of the same id stays as a fail-closed guard: were the context ever without the id, the template would fall back to the bare name, which the
model can set (`api/utils/template_renderer.py:192-196`), and the empty preset refuses the request first (`custom_tool.py:263-269`, `:407-427`). `test_golive.py`
runs 1.51's own `execute_http_tool` offline on both committed tools to show it, and go-live refuses a tool template without that body template. The tool posts
to Synchronize's `/api/voice/tools/caller-work` with the same `X-Dograh-Secret` Custom Header credential and times out after 5000 ms. The answer holds only
`known`, `name` and `upcoming_visits`: up to five distinct days of the caller's next visits, earliest first, each `{"date": "YYYY-MM-DD", "spoken": ...}`
with `spoken` already in the company language's spoken form (`11월 10일 월요일`, `Monday, November 10`) in the workspace time zone, so the model
neither reads digits nor works out a weekday. No count or due date of internal Tasks (A2): they are the staff's work, not the customer's
appointments, and a receptionist tells a caller only their own next visits (Jobber's AI receptionist gives the next appointment, ServiceTitan's
voice agent confirms existing appointments). No task text, project or site names, estimates or amounts either (amendment A1 §2). The tool
description says exactly that, that the lookup is for a caller who confirmed the name on the caller record and asks when their next visit is,
that the days may be told to that caller as spoken, that anything else is taken as a message, and what to say when the lookup does not answer
(Prompt behaviours, below). Dograh
says `확인해 보겠습니다.` / "Let me check." when the lookup starts and `잠시만 기다려 주세요.` / "One moment, please." if the answer takes over 2.5 s
(`api/services/workflow/tool_messages.py:97-124`). The Korean Detail line and the English Reception line say the phone system speaks the first one, so the
turn that calls the tool says nothing (English also: the caller hears it from the system, never from the agent, so the step-1 answer does not say it
either) and the caller does not hear it twice. The tool is on the Korean Detail node and on the English Reception node, where the prompt places it after step 1. The call reaches the Detail node after the name step also when
the caller declines a name or gives another one (edge `name_given`), so only prompt text, not structure, keeps a lookup result from a caller who did not
confirm the name: the tool description's when-to-use sentence keeps the lookup from running, and the Persona's record rule keeps a result from being told.
The runbook's test call 2 (its unconfirmed variant) checks it live; the text-chat eval checks that an unknown caller's question, and a known
caller who will not confirm the name, run no lookup (a text-chat lookup answers `{"known": false}`).
When to use it lives only in the tool's description; the node line says only the order (look up before the node's question, ask that question on the next
turn). Tools are organization objects referenced by UUID: the templates name it with the reserved placeholder `00000000-0000-4000-8000-000000000101` (ko) /
`…0102` (en), which `golive.py` replaces with the real tool's UUID; Dograh would silently drop the placeholder, and go-live refuses to publish it. Call QA
cannot check that a relayed record is faithful: Dograh's QA transcript shows a lookup only as `[tool_call]: <name>` and never shows its result
(`../UPGRADE-1.51.md`, Call QA transcript). `PROMISED_UNCONFIRMED` therefore keeps "stated or promised" for a price, schedule, availability, feasibility, the
status of existing work, a contract or payment fact and a booked visit, and lets only a neutral "the record shows …" sentence about the visit days
pass, and only after a `[tool_call]` line for the lookup and to a caller who confirmed the name on the caller record: since the record reaches the call only
through the lookup, the same sentence with no lookup before it is invented. The Korean confirmation is in the Greeting node and the relay in Detail, and
Dograh judges each node on its own transcript, so the QA prompt carries Dograh's `{{previous_conversation_summary}}`: Dograh writes that summary of the call
before the node for every node after the first, whether the prompt uses it or not (`api/services/workflow/qa/analysis.py:196-215`), and its default QA
prompt has the same section (`api/services/workflow/node_specs/constants.py:6-7`). `LONG_TURN` leaves out the fixed opening greeting: Dograh logs the
greeting as a bot-text event under the start node (`api/services/pipecat/realtime_feedback_observer.py`, `log_greeting`), so the start node's QA reads it, and
both greetings are three sentences. Faithfulness is checked only by the runbook's test call 2. Prompt text for this: the `전화한 분` / `Caller` name line,
the role line naming the lookup, the record exception in `하지 않는 것` / `What you never do`, the tool description's use and relay sentences, the
confirm-the-name sentence in the start step, the confirmed-name clause of `customer_name`, the Detail / Reception lines on lookup order and on the spoken
start message, and the QA sentences. Only the text-chat evals below measure it.

The settings carry six outcomes, each with a classifier description in the workflow's language: `quote_request` (new quote or price), `booking_request`
(visit, measurement or appointment, including one needed for a quote), `message` (anything else the owner must check or return: the status of existing work,
a complaint, a partner's or supplier's message, a call-back request), `done` (nothing left for the owner), `spam` (sales, ads, robocalls) and `wrong_number`.
Urgency is not an outcome but its own flag: every conversation node (Korean 1, 2 and 3; English 1) extracts `urgent`, a boolean whose prompt is
`누수·가스·안전처럼 바로 사람이 봐야 하는 일이면 true, 아니면 false.` / "true if a person needs to look at it right away, such as a leak, gas or a safety problem;
otherwise false.", so an urgent quote stays a quote (amendment A1 §3). Synchronize shows the code as the call's label, a separate 긴급 badge when `urgent`
is true, and needs no follow-up for `done`, `spam` and `wrong_number`; every other code and every unclassified call needs one.
`scenarios_caller.py` measures these through the text-chat API (Evals, below).

## Prompt behaviours (A2)

Four behaviours, written once as a general rule in both languages and placed in the line the model already reads for that moment, not as
per-failure sentences. None is measured yet: the runbook's step 6 measures them before the Korean number is routed.

- **The lookup does not answer.** When it fails, times out (Dograh answers `{"status": "error", ...}`, `api/services/workflow/tools/custom_tool.py:513-551`)
  or answers `known: false`, the agent says the record cannot be checked now and that the person in charge (English: the owner) will confirm
  and call back, and does not call it again. Guides differ on a retry (Vapi tries once more, OpenAI's voice guide says not to repeat a call);
  one 5 s wait is already long on a phone, so no retry. It is in the tool description, the one text that describes the result; an empty
  `upcoming_visits` gets the same hand-over without claiming that nothing is booked.
- **A question at the close.** The Korean Closing node does not inherit the global rules, so its own line says: a question about price,
  schedule or whether something is possible gets only that the person in charge will confirm it, and no answer adds a promise or a fact the
  call did not give. It is written in the line's indirect form (`담당자가 확인해서 알려 드리겠다고만`), so the model says it at the 합니다체 level the
  global node sets for promises, not as a quoted sentence (a full quoted sentence once made a step close early, above). English has no Closing
  node; step 4 of `Reception` says the same for a question after the recap.
- **A caller asks for a person.** The hand-off rule now says that no one can take the call right now and asks once for a convenient time to
  call back (CALLBOTO does the same when it cannot connect); a caller who is only upset still gets "cannot connect now, the owner will get
  back to you". The time goes into `follow_up` (its prompt in every node now asks for it), not a new variable: like the caller type in
  `facts`, Synchronize already shows `follow_up` on the call and passes every extraction field to the agent, so a new field would add a column
  nothing reads first. The steps leave room for the question in both languages in the same place: the first step answers a request for a
  person with "no one can take the call right now" and asks the name, and the one follow-up question (Korean `Detail`, English step 2) is the
  callback time when a person was asked for and no time was given. A request for a person that comes only as the answer to that question gets
  the callback-time question before the close (Korean `Detail` and its `done` edge, English step 3).
- **Another callback number.** When the caller gives a number other than the one they are calling from, the agent reads it back once in short
  groups of digits and asks whether it is right (under `잘 안 들릴 때` / `Unclear audio`, next to "take numbers as heard"). Vapi advises against
  read-backs in intake calls, so this is limited to the one number the owner will dial. The rule is also where the call moves on, because the
  most natural moment for the number is the answer to the last question: Korean `Detail` reads it back before the close and its `done` edge
  fires only after the caller answered the read-back (the Closing node does not inherit the global rules and, as a Dograh 1.51 end node,
  speaks once and hangs up, `api/services/workflow/pipecat_engine.py:469-487`); English step 3 reads it back before the recap, which itself
  reads no number. The Korean Closing then says `말씀하신 번호` instead of reading it again.

## Business facts and hours

Each company sets in Synchronize (전화 > 연결) a service area, a pricing-policy line (each at most 200 characters, validated like the business name), weekly hours
with closed dates, in the owner's time zone. Synchronize's pre-call sends them per called number in `initial_context` (Synchronize branch
`fm/261108_voice_us_receptionist`):

| Key | Value |
|---|---|
| `service_area`, `pricing_policy` | the owner's text, as written |
| `open_now` | `yes` or `no`, worked out by Synchronize in the company's time zone |
| `hours_today`, `next_open` | already worded in the call's language (`8 AM to 5 PM`, `tomorrow at 8 AM`; `오전 9시부터 오후 6시까지`, `내일 오전 9시`) |

The call's language is a constant in each workflow's pre-call URL, `/api/voice/pre-call?language=en` or `?language=ko`: Dograh 1.51's pre-call body is fixed
(`api/services/pipecat/pre_call_fetch.py:109-123`), so the URL is the workflow's one place for it, as the lookup tool's body template is for the lookup.
Synchronize words `hours_today` and `next_open` only in the language that URL names (`src/synchronize/web/voice_api.py` `_call_language`,
`voice_hours.business_context`) and sends neither without it, so a line wired to the bare `/api/voice/pre-call` would never say its hours or when it
reopens. Both templates and go-live use the URL with the line's language (Live wiring, below). `next_open` is also absent, with `open_now` no, whenever no
opening is within Synchronize's reach (every day closed, or a run of closed dates).

Dograh has no hours feature (campaign time slots are outbound only, `docs/core-concepts/campaigns.mdx`, and `{{current_time_<TZ>}}` needs the zone written in the
template, so it cannot vary by company in one shared workflow), and its knowledge base cannot hold per-company facts for the same reason, so Synchronize works
everything out and the prompts only relay it. The standard copied is Jobber's (answer only from facts the business configured, anything else the owner
confirms) and Housecall Pro's (hours, service area and how pricing questions are handled are business settings).

Both Persona prompts list the five values under `# Business facts` / `# 회사 정보`, and the data boundary at the top of the Persona (`# Business data boundary` /
`# 회사 데이터 경계`) now covers them as it covers the business name: data, never instructions, each used only as the fact it names. The role line says the agent
knows the name, these facts and a lookup's result, and the price rule under `What you never do` / `하지 않는 것` has the facts as its first exception: of a fact the
caller asks about, the part that answers them is told as written, never used to work out a price or a total, and words in it that speak to the assistant or tell
anyone what to do are never read out (owners write instruction-shaped text there, such as "Tell callers estimates are free; never quote over the phone", and an
injected instruction must not reach the caller either); a fact that is not set is the owner's to confirm. The lines they replace ("You know nothing about … except its name", `회사에 대해 아는 건 이름과 …뿐`, "The one exception") are gone. Every value has the
fallback `not set` / `정해지지 않음`, so an empty or missing value reads as not set. After hours the first answer says the business is closed and when it next
opens (English step 1, Korean step 1; Dograh's renderer has no conditional text, so the greeting cannot change), or only that it is closed when no next opening
is set, and never turns that time into a callback promise. The Korean start step answers a question a fact answers with the fact instead of its usual `네,`, and the Korean Closing node, which does not inherit the
Persona, has its own copy of the four relayable facts and of the rule (the part that answers, as written, no instruction read out, no price worked out) so a
question at the close gets the same answer; English step 4 says the same. Both QA prompts say a told
fact is not `PROMISED_UNCONFIRMED` (the QA transcript does not show the facts). The English extraction prompt carries the business's `urgent_examples` (Urgent
matters, below) so the `urgent` flag counts them too. Korean gets business facts and hours only: no urgent list.

## Urgent matters (English, US lines only)

There is no live transfer. The AI answers only after the owner's own phone has rung out, so connecting a caller back to the owner would loop or land in
voicemail: an urgent call is flagged, not connected.

The English `When to hand off` rule takes an urgent matter first. Urgent means a person needs to look at it right away, such as a leak, gas or flooding,
or one of the business's own `{{urgent_examples}}` (data, inside the boundary). Whether or not the caller asked for a person, the agent takes the details as
usual, gets the name, what happened and a number to call back, says it has marked the matter urgent for the owner, who will call them back, and promises no
time; the call is never connected to anyone. Step 3 of Reception asks once for a number to call back when the matter is urgent and the caller gave none
("this number" is an answer). The English QA prompt counts an offer to connect, transfer or put the caller through to anyone as `PROMISED_UNCONFIRMED`; the tag
holds no exemption for it. The `urgent` extraction variable, whose prompt carries the business's examples, is what Synchronize shows as the call's 긴급 badge. A caller
who asks for the owner or a person gets "no one can take the call right now" and a callback time; a caller who is only very upset gets the same, and that the
owner will get back to them. Steps 1–3 of Reception point a request for a person, and an
urgent matter, to that rule, and ask for a callback time when a caller asked for a person and has neither asked nor said when suits them.

## Kinds of call, visits, recording and language (English, US lines only)

All of it is general rules in the lines the model already reads, not one sentence per case; the six outcomes stay as they are.

- **Kinds of call.** After the name, Reception step 2 asks what the owner needs for the kind of call, judged from what the caller said, one
  question per turn and only what they have not said: a new project, the service address (read back once), the work and when; a project under
  way or a change to one, which project or address and what changed; a problem with finished work or a warranty matter, what is wrong and since
  when. Any other call keeps the one question that builds on what the caller said (or the callback time when a person was asked for).
- **Visits.** Moving a visit takes the day and time window wanted instead; cancelling first offers once to find another time, then asks why.
  The owner confirms either; the agent never confirms, books or moves a time (the approval card after the call is Synchronize's). Both are
  `booking_request`, and `follow_up` carries the window.
- **Recording.** The greeting says the call is recorded. A caller who does not want that hears that recording cannot be switched off on this
  line, that the call ends now and that the owner will call them back at this number; the agent asks nothing more and ends the call with
  `end_call` on its next turn (a turn is speech or a tool call, never both).
- **Language.** The line takes calls in English only (Spanish is deferred). A caller who speaks another language, or asks for one, hears once, in
  that language, only that the owner will call them back at this number (Spanish: `El dueño le devolverá la llamada a este número.`), then
  `end_call`'s fixed English goodbye. Speech recognition runs in English (below), so on a call the model reads a Spanish caller's words as
  English recognition heard them; text chat cannot show how well that works, and no real call has measured it.
- **AI.** Whenever the caller asks whether it is a real person or a robot, the agent says plainly that it is an AI assistant and carries on.
- **Speech.** Numbers, times, prices and addresses are said as words; the name is read back once by using it in the next question, a new
  project's address and another callback number once; nothing is spelled letter by letter, and only a part the caller corrected is spelled,
  once. "I'm sorry" only when declining, deferring to the owner or asking again. The greeting cannot be interrupted
  (`allow_greeting_interrupt` false: caller speech is kept until it ends, `api/services/workflow/dto.py`); interruptions stay on after it.
- **Values for Synchronize.** Node 1's extraction, which already carries `urgent`, also carries `recording_declined` (boolean, true only when the
  caller said they do not want the call recorded) and `caller_language` (the BCP-47 code, such as `es`, only when the caller did not speak
  English; empty otherwise). Dograh writes every extracted variable at the top of the run's `gathered_context`
  (`api/services/workflow/pipecat_engine.py:697-707`), so Synchronize reads `gathered_context.recording_declined` and
  `gathered_context.caller_language` where it reads `gathered_context.urgent`. The extraction writes a phone number as E.164 and a date the
  caller named by its day as `YYYY-MM-DD` (dated by Dograh's `{{current_time}}`, which is UTC); a day named relative to the call (tomorrow,
  next Tuesday) stays in the caller's words, because one shared workflow does not know the business's time zone and UTC would move an evening
  call's "tomorrow" by a day.

## Model, speech recognition and call limit (English)

`consultation-en-settings.json` carries them; go-live applies them (Live wiring, below).

- **Model.** `model_configuration_override.llm` is Google `gemini-3.6-flash`. 1.51 takes a Google model id as free text (`allow_custom_input`,
  `api/services/configuration/registry.py` `GoogleLLMService`) and already treats Gemini 3.6 and later (it sends no temperature,
  `api/services/configuration/temperature.py` `GEMINI_RULES`; `api/tests/test_llm_temperature.py` runs `gemini-3.6-flash`).
- **Fallback.** `llm_fallback` is Dograh's FallbackPolicy with two rules, both to Claude Haiku 4.5 on AWS Bedrock
  (`us.anthropic.claude-haiku-4-5-20251001-v1:0`, in 1.51's own Bedrock list; 1.51 tests Bedrock as a fallback with tool calls,
  `api/tests/test_llm_fallback_providers.py`): no output after 1500 ms, a hedged race in which Haiku starts while Gemini may still answer, and an
  error. One request serves both rules (`api/services/pipecat/service_factory.py` `create_llm_service`).
- **Speech recognition.** `stt` is Deepgram itself, `nova-3-general`, language `en`. Dograh's managed STT would send `en` to Flux.
- **Call limit.** `max_call_duration` is 1200 s, Dograh 1.51's ceiling (`api/schemas/workflow_configurations.py`). 1.51 has no wrap-up before
  the limit: the call monitor ends the call at once when it passes (`api/services/pipecat/call_monitor_processor.py` `_check_call_duration`,
  `api/services/workflow/pipecat_engine_callbacks.py` `create_max_duration_callback`, `abort_immediately=True`), and no template variable gives
  the model the time spent, so the prompt cannot wrap up either.
- **Connections.** Each service names its provider connection by a reserved placeholder UUID (`…0201` Gemini, `…0202` Bedrock, `…0203`
  Deepgram, `golive.py` `MODEL_CONNECTIONS`), which go-live replaces with the UUID of the active connection named `synchronize-gemini` (google),
  `synchronize-bedrock` (aws_bedrock) or `synchronize-deepgram` (deepgram). The keys live in those Dograh connections, typed at silent reads
  (runbook step 5), never in JSON or git. The line's named configuration (`model_configuration_uuid`), and with it the voice, stays as the
  operator set it; the override's services lie over it (`api/services/configuration/model_connections.py` `_apply_model_patch`).

## Deployment values and authentication

For a new import, substitute the values below. For existing customised live workflows, use **Live wiring without re-importing** below.

Set `DOGRAH_WEBHOOK_SECRET` on the Synchronize backend to a shared secret. In the same Dograh organization as the workflow, open **Credentials**, create a credential
with type **Custom Header**, set **Header Name** to `X-Dograh-Secret` and **Header Value** to that exact secret, and save it. Copy its UUID (the credential reference, never the secret)
into node `1` → `data.pre_call_fetch_credential_uuid`, replacing `<DOGRAH_WEBHOOK_CREDENTIAL_UUID>` in each deployment JSON. The API credential type is `custom_header`,
with `credential_data.header_name` and `credential_data.header_value` (see `api/utils/credential_auth.py`). Keep secrets out of workflow JSON and git.
Replace `https://synchronize.example.invalid` in node `1` → `data.pre_call_fetch_url` with this deployment's public Synchronize backend origin, preserving
`/api/voice/pre-call?language=<lang>` (`en` or `ko`, as the template has it; without it Synchronize sends no hours, Business facts and hours above).
This is an operator substitution before import, not a call variable; `.invalid` deliberately cannot name a deployed service. In the editor, Start Call → Advanced Settings must show
**Inbound calls only**, that URL, and the saved credential under Authentication. Dograh sends JSON with `call_inbound.from_number`, `call_inbound.to_number` and `call_inbound.workflow_run_id`;
Synchronize returns variables only. The workflow does not request greeting or model overrides. If the request fails, times out, returns invalid JSON, or returns `{}`,
Dograh continues with earlier context and the generic greeting fallback (`docs/voice-agent/pre-call-data-fetch.mdx`).

The caller-work tool comes next, before import. In `tool-lookup-caller-work-{lang}.json` → `tool`, replace `https://synchronize.example.invalid` in
`definition.config.url` (keep `/api/voice/tools/caller-work`) and `<DOGRAH_WEBHOOK_CREDENTIAL_UUID>` in `definition.config.credential_uuid` with the same origin and
credential UUID. Create the tool from that object (`POST /api/v1/tools/`, or Tools → new HTTP API tool with the same fields), then put the returned `tool_uuid` in
place of the placeholder `00000000-0000-4000-8000-000000000101` in node `2` → `data.tool_uuids` (Korean) or `…0102` in node `1` (English). Dograh looks a node's
tools up among the organization's active tools only (`api/db/tool_client.py:294-319` at 1.51), so a placeholder left in place publishes without an error and the
lookup never runs. For an existing workflow, `golive.py --apply` does all of this (Live wiring, below).

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
Of the settings template go-live applies only the settings it owns (`golive.py` `SETTINGS_KEYS`: `call_dispositions` and `max_call_duration`; of
`model_configuration_override` the services `MODEL_KEYS`: `llm`, `llm_fallback` and `stt`), each only by its old-ref → new-ref delta: live equal to the
old template is `template-replaced`, an unset value is `set`, anything else is `REPLACES-CUSTOM` and needs `--accept-merged` with the printed label
(`settings.call_dispositions`, `settings.max_call_duration`, `settings.model_configuration_override.llm` and so on). Every other setting, the turn settings
included, and the override's named configuration stay as they are. A model connection the template names by placeholder (Model, speech
recognition and call limit, above) is resolved by its name before any comparison; a missing, duplicate, foreign or archived one stops the plan
before any write, and no placeholder is ever published.
Stable node IDs/types preserve renamed nodes, custom nodes, edges, positions and tools. Template changes to node data and to edge data (`edge 1-2.data.condition`)
merge the same way; a template that adds, removes or re-targets an edge is refused before any write.

The caller-work tool (`tool-lookup-caller-work-{lang}.json` at the new ref) is found by its unique name among the organization's non-archived tools: absent → created,
equal → untouched, different description/icon/definition → updated in place (name and category never change) with a mode like the fields': `wiring` when only the URL or
credential differs, `template-replaced` when the live tool equals the old ref's tool, and `REPLACES-CUSTOM` otherwise (edited in Dograh → Tools, or an old ref without the
tool), which needs `--accept-merged tool.<name>`. Its URL is the runtime origin + `/api/voice/tools/caller-work` and its credential is the named `--credential-name`
credential. A duplicate name, or a tool of that name that is not an active tool of the template's category, aborts before any write.
A tool whose icon is `synchronize-test-copy` (`TEST_COPY_ICON`) is a copy `scenarios_caller.py` made for an eval run (the failing lookup): it has the line tool's own
name, because that name is the function the model calls, and it is otherwise the planned tool exactly, so without the mark go-live would call it unchanged, attach
it, and lose it when the eval archives it. Go-live refuses it before any write
and names `scenarios_caller.py cleanup`. The icon is the one tool field neither the model nor Dograh's UI reads (the UI draws a tool's icon from its category).
The tool reference is wiring too: every node the new
template gives the tool references it once (from any old ref, with or without the tool), a reference to an archived tool of that name is replaced where it stands, other
tools on the node stay, a template change to those other tools on that node is refused, and a reference Dograh
cannot return aborts the plan. The published definition and the tool are both read back. A failed apply after a tool update prints the `--rollback` command even when the
workflow is unchanged, because an updated tool is live at once.

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
and each tool that run manages as it was (or `null` when it did not exist): the caller-work tool.
The version/ID and path are printed; apply explicitly prints **`ROLLBACK FILE: <path>`**. Keep that file for recovery.
Backups never contain the supplied secrets; workflow data containing either is refused. QA/provider secrets remain masked by Dograh's API.
Plan has no remote writes. Each changed template field prints
`node N.data.FIELD: template-replaced; -R/+A changed lines` when live equalled the old template, or
`node N.data.FIELD: MERGED-INTO-CUSTOM; -R/+A changed lines` when a custom field was merged.
Custom merges include a zero-context diff of changed text lines for review; unchanged prompt lines are withheld.
Other template fields with no actual change are omitted. Operator wiring fields use `wiring` in the same format;
rollback fields use `restore-backup`. URL paths omit origins; secrets are never shown.
`settings.call_dispositions: MODE; codes` prints the old and new code lists (descriptions withheld), every other owned setting `settings.LABEL: MODE` with its
old and new value (a duration, model ids and connection UUIDs; no secret), and `tool NAME: create|update FIELDS|unchanged` the tool change.
The all-zero UUID denotes a credential that does not yet exist, and the `00000000-0000-4000-8000-` family the template's tool placeholders; none is ever published.

`--apply` requires `DOGRAH_WEBHOOK_SECRET` from the environment only. It creates or rotates the uniquely named `custom_header` credential,
using `header_name = X-Dograh-Secret`. Use a dedicated shared credential: rotation affects every consumer.
Before any remote write, apply refuses every `MERGED-INTO-CUSTOM` field unless explicitly named with repeatable
`--accept-merged FIELD`. After reviewing the plan's diff, use e.g.
`golive --apply --accept-merged 'node 1.data.greeting' --accept-merged 'node 4.data.prompt'`.
Accept only the exact fields printed as merged; the flag never bypasses an ambiguous/conflicting merge or the published/draft guards.
If the planned definition and the settings it owns already equal live, the tool is unchanged and the named credential exists, it says **Already wired** and skips rotation, PUT and publish;
it cannot compare a masked stored secret, so changing the environment secret alone does not rotate an already-wired workflow.
Writes run in this order: credential create/rotate, tool create/update and tool read-back, wiring validation, draft save (definition, plus settings only when a
setting it owns changes), publish, read-back. A tool-only change saves no draft. In 1.51 the PUT only saves a draft and only the published version runs,
which is why every apply publishes and reads back; the diff is always against the published version, because the fetch it reads returns a draft when
one exists and go-live refuses one at backup and again before writing.
Start node 1 gets the exact `inbound` mode, the URL `/api/voice/pre-call?language=<the line's language>` and the authentication; a URL without that language
(as go-live wired it before, or as edited by hand) is wiring and is set back. Webhook node 6 is copied from the template if missing;
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
It posts to the pre-call URL go-live wires, with `?language=` the workflow's language, and mirrors `api/services/pipecat/pre_call_fetch.py:109–123`:
`event = call_inbound`, integer `agent_id` from `--workflow-id`, synthetic caller `+12025550100`,
called E.164 number, integer `workflow_run_id = -2147483647`, and `Idempotency-Key: dograh-pre-call--2147483647`.
The Synchronize runtime source (`src/synchronize/web/voice_api.py` in the main repo) ignores pre-call agent/run IDs and uses only the called number;
the negative pre-call ID deliberately cannot name a real Dograh run. Probe expects 200 JSON with the environment secret and 401/403 with a wrong secret.
It also probes `/api/voice/call-ended` with `{"workflow_run_id":"2147483647"}` and the same correct/wrong secret headers.
This is the largest value Dograh's 32-bit `workflow_runs.id` column can hold, so the lookup answers a clean 404 instead of a database range error (an 18-digit id would make Dograh answer 500 and the probe fail on a correct deployment). `"0"` fails validation (the route still answers 200 `{}`),
so it would not exercise the lookup path. For the deliberately unknown positive ID, `Voice.owning_tenant` (`src/synchronize/voice.py`)
maps Dograh's missing-run 404 to `None`; the completion route returns **200 `{}` without enqueueing a notice**. A lookup failure instead returns 503 and fails the probe.
It then posts `{"workflow_run_id": 2147483647}` to `/api/voice/tools/caller-work`, a JSON integer as the tool's body template sends it, and expects 200
`{"known": false}` (an unknown run) and 401/403 with the wrong secret. Synchronize answers a body it refuses with the same 200 `{"known": false}`
(`src/synchronize/web/voice_api.py`, `caller_work`), so this proves the route and its secret, not that the integer id is accepted: test call 2 proves that.
Successful probe output is six status-only lines in order: pre-call correct secret `200`, pre-call wrong secret `401`/`403`,
call-ended correct secret `200`, call-ended wrong secret `401`/`403`, caller-work correct secret `200`, caller-work wrong secret `401`/`403`. A 404, 422 or 5xx at any route fails,
so a runtime without the caller-work route stops the go-live before anything is wired.
An older runtime that silently drops digit-text ids also answers 200 `{}`, so this probe cannot tell the two receivers apart: only the test call proves that completions are processed.
The runtime's completion lookup reads Dograh; it does not create a run. The deployed runtime's behavior and absence of that synthetic ID cannot be established offline.
The test call must still verify business context, a saved call and authenticated completion separately.

The operator sequence with exact commands is `../RUNBOOK-20261108.md`: step 0 (shell, `golive()` function), step 5 (probe, plan, apply) and R1
(rollback). Two orders matter there. The runtime must hold `DOGRAH_WEBHOOK_SECRET` and be deployed and healthy before `--apply`: pre-call auth errors fall back
to empty context (`api/services/pipecat/pre_call_fetch.py:204–214`) and completion auth errors dead-letter (`api/tasks/webhook_delivery.py:327–330`), so wiring
Dograh first can lose completions. And `golive.py` reads the templates from `HEAD`, so run it from a clean checkout of the reviewed commit.

Offline targeted proof: `python3 synchronize/workflows/test_golive.py` (standard-library unittest, loopback fake server; no pytest or external calls).
It covers both languages, draft refusal at both reads, private versioned backups, Korean greeting/conflict handling, silent-skip regressions,
create/rotate/no-op, every failure stage and uncertain writes, sanitizer/QA-mask fidelity, rollback with a pending draft/settings diffs, Unicode hints, durable paths and exact probe payload/auth,
tool create/update/unchanged/ambiguous/inactive, tool restore on rollback, format-2 backups, the owned settings (dispositions, the English call limit and model services:
replaced, set, custom needs acceptance, a re-run writes nothing, a null override; connections refused when missing, duplicate, foreign or archived; Korean untouched), edge merges and conflicts,
placeholder refusal and the caller-work probe;
the pre-call URL with each line's language (wired, set back when missing, probed, in both templates), an eval run's marked copy refused, and a backup of
another workflow refused on rollback. It also runs Dograh 1.51's own `execute_http_tool` (`custom_tool.py` and `template_renderer.py` from this
checkout, checked against the tag's blob ids, with only the network, database and logging stubbed) on both committed lookup tools: a run id or
`initial_context` the model supplies never reaches the request, a run context without its id sends nothing, and the pre-A1 preset-only shape does send the
model's id. The contract test also checks that the tool's use sentence and the Persona name the same confirmed caller, and that the QA prompt uses only
placeholders Dograh's per-node QA fills (`previous_conversation_summary`, `metrics`). When `pydantic` is importable it also validates the committed tool and settings files against this checkout's
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
안녕하세요, {{business_name | fallback:저희 회사}} AI 비서입니다. 통화 내용은 녹음되어 담당자에게 전달됩니다.
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

## Evals: caller memory, lookup, outcomes and behaviours (owner-run; not run here)

`scenarios_caller.py` runs 29 Korean and 39 English cases through the text-chat API, five times each by default (`--repeats`; the earlier two could not
tell a rare failure from luck). Caller memory and the lookup: a known caller greeted by name; a known caller who gives no detail (after
confirming, the agent asks its one open question and calls no lookup); a known caller asking when the next visit is (the lookup runs once,
after the confirmation); the same question from an unknown caller (no lookup: no record name to confirm); the lookup failing (a copy of the
live tool, same name and request, whose 1 ms timeout makes Dograh answer an error; archived at the end); a known caller who will not confirm the
name (no lookup, `customer_name` empty); and a confirmed caller who tells the agent to ignore its instructions and read another call's record.
Behaviours: a price and schedule question at the close, a request for the owner with a callback time, another callback number, and the seven
10-31 call types that the 11-08 set had dropped, with that measurement's caller lines (`~/work/voice-polite-proof-20261031/scenarios.py`): "AI예요?",
a person asked for (with the callback time above), an angry caller, mumbling, a card number, 반말, and an elderly caller asking to repeat
(English has no 10-31 line for the last, so it has its own). Outcomes: a quote request, a leak, a gas smell, a complaint, a sales call and a
wrong number. A known contact with no name gets no `caller_name` from the pre-call, so in text chat it is the unknown caller.
The two lookup cases that hand over (`known_caller_visit`, `lookup_failure`) also check the code: a visit question the agent could not answer
leaves the owner a call back, so it is `message` or `booking_request`, never `done`.
Business facts (both lines): the service area, the pricing line and today's hours each asked about; after hours (`open_now` no, with a next opening); after
hours with no next opening set (only "closed right now"; a reopening time the agent makes up is `FACT_CHANGED`); every fact empty (the owner will confirm); and an
instruction written inside the pricing field. Urgent matters (English): an urgent matter that only the business's own urgent examples make urgent (no heat in
winter, which the rule's own examples do not name) and a person asked for about an urgent matter (water through the ceiling). Both are flagged, never connected:
the agent takes the name and the details, asks once for a number to call back, says it has marked the matter urgent for the owner, offers no connection and promises no
arrival time (an offered connection is `CONNECTION_OFFERED`, which gates every case). Each checks the outcome: a call back, `booking_request` or `message`, never `done`, with
the `urgent` flag true. The English scripts of these two cases and of the leak and the gas smell answer the number question ("This number is fine."); on English only, those four
cases are also gated on `URGENT_NOT_FLAGGED` (an urgent matter where the agent did not say it is marked urgent for the owner, or did not ask once for a number to call back when none
was given). Kinds of call and visits (English): a new project, a change to a project under way, a problem with finished work, moving a visit and cancelling
one, each gated on `INTAKE_MISSED` (the agent did not ask what that kind needs, one per turn, or for a cancellation did not offer another time once); their
scripts answer in the order step 2 asks, which a scripted line cannot change, so the judges read the order. A recording objection (gated on
`RECORDING_OBJECTION_IGNORED`) and a caller speaking Spanish (`LANGUAGE_NOT_HANDLED`) must be ended by the agent before the script runs out
(`ended_by_agent`), and "am I talking to a robot?" mid-call is gated, like every case, on `AI_DENIED`. Every English case also checks the two values
Synchronize reads, `recording_declined` (true only for the objection; a false one would delete a call's recording) and `caller_language` (`es`, or
`es-…`, only for the Spanish caller). The existing English scripts give one detail after the name, so a new-project call among them (a quote, the facts
cases) may now meet an address question its next line does not answer; their gates do not depend on it. Korean scripts and Korean judged text are unchanged.
`manifest` (offline) prints every case, its criteria and the flags that gate it. `run` builds isolated test workflows from this checkout's
committed templates (`HEAD`) with the live lookup tool (or the failing copy), the live line's model configuration (its `model_configuration_override` as go-live
applied it, never the template's placeholders) and the template settings, every case but the lookup failure on the line as golive deploys it;
it removes the pre-call fetch, the completion webhook and the QA node, archives everything it created at the end, and never writes to the
live workflow or the live tool. It scores the objective checks: the opening text; for a known caller the name in the first reply and
`customer_name` equal to the confirmed name (empty when the caller does not confirm); the lookup called exactly once and never before the
confirmation line when a known caller asks about a visit, also when it fails, and not called when they never ask, will not confirm, or are
unknown; for each outcome case and the two hand-over lookup cases the code (the leak and the gas smell may be `booking_request` or
`message`, a complaint is `message`) and the `urgent` flag; on English, every case's `recording_declined` and `caller_language`, and that the agent itself
ended the recording objection and the Spanish call. Every session file keeps a review block: the replies, the lookup's arguments and results, and the extraction for the owner.
`cleanup --out DIR` archives what a crashed run left (`DIR/state.json`, workflows and the failing copy).

The judged criteria go to independent judge models, in the format of the 10-31 blinded-judge harness (`build_judge.py`, `score.py` in
`~/work/voice-polite-proof-20261031`). `judge --out DIR` writes `judge.md`: a fixed flag rubric (the 10-31 flags plus record, lookup,
callback, closing, mumbled-call, guessed-value and AI-answer flags, and business-fact, field-instruction, after-hours and offered-connection flags, and on English
`URGENT_NOT_FLAGGED`, `INTAKE_MISSED`, `RECORDING_OBJECTION_IGNORED` and `LANGUAGE_NOT_HANDLED`, whose rubric clause on urgent matters, kinds of call, the
recording and other languages the Korean rubric does not have) and every transcript as a numbered
item with the caller record name, the business facts, on English the business's urgent examples when set, the lookup calls and results and the after-call
`follow_up`, in a deterministic shuffle with planted canaries (a price stated at the close, a record invented after a failed lookup, 반말 or curt speech, a price
worked out from the pricing line, on English a connection offered to a caller who asked for the owner, an urgent matter neither marked urgent for the owner nor asked for a callback number,
a new project with no address, work or timing asked, a recording objection answered with "the recording is off" and more questions, and a Spanish caller answered in English, and clean calls: a failed lookup handled, a caller who declines the
record's name after the agent asked once whether they are that person, and on English an urgent matter flagged for the owner without a connection and a Spanish caller told once in Spanish before the
fixed English goodbye, which a judge must not read as `WRONG_LANGUAGE`; six Korean, thirteen English), and no case name or criterion; `judge-key.json` maps items back and
must not reach a judge. Canaries go through the same rendering as the real sessions, with Dograh's result shape and the tool's spoken start
message, so no line format marks them. The judges run in an empty directory (runbook step 6), as the 10-31 judges did, because the fork
holds every case and canary word for word.
`score --out DIR` reads each judge's answer from `judge.<name>.out` (a JSON array), counts a judge only when it answered every item, caught
every bad canary and left the clean ones without a gate flag, and fails a session when every counted judge applied at least one of that
case's gate flags, not necessarily the same one (one defect often fits two flags, such as a price at the close: PROMISE or CLOSING_ANSWERED);
it needs two counted judges to decide. Flags that gate no case are counted per judge in `judged.json`. The 10-31 harness compared two versions pairwise; this one has a single version, so each transcript
gets flags instead of a winner.

A text-chat run is not an inbound call, so Synchronize's caller-work route answers `{"known": false}` to it: the visit case measures that the
tool is called once and that the agent hands over ("cannot check now, the person in charge will call back") without promising; relaying real
records is checked on the second test call of the runbook. Text chat also does not recover tool calls the model wrote as text
(`../UPGRADE-1.51.md`). The commands (after golive `--apply`, with the two connection values exported) are in the runbook, step 6:

```bash
python3 synchronize/workflows/scenarios_caller.py run --lang ko --live-workflow-id 1 --out "$HOME/voice-evals/caller-ko-$(date -u +%Y%m%dT%H%M%SZ)"
```

Offline proof of the runner and the judge format: `python3 synchronize/workflows/test_scenarios_caller.py` (loopback fake text-chat API).

`voice_ab.py` makes the blind voice comparison of the runbook's step 8: the Korean line's own sentences with `eleven_v4_turbo` and Flash
v2.5 on one voice, in Dograh's call settings (stability 0.8; similarity 0.75 for Flash, `api/services/pipecat/service_factory.py:976-991`),
v3/v4 through Text to Dialogue as on a call, as 8 kHz mu-law WAV pairs with A and B drawn at random per sentence, a sheet per listener and a
key the listeners never see. Offline proof: `python3 synchronize/workflows/test_voice_ab.py` (loopback fake ElevenLabs).
