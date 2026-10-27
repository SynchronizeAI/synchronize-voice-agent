# Consultation workflows

The AI phone consultation, ready to load: `consultation-ko.json` and `consultation-en.json` are the body of
`POST /api/v1/workflow/create/definition`; `*-settings.json` is the body of `PUT /api/v1/workflow/{id}` (call limit
300 s and the two dispositions; add `"dictionary": "<business name>"` per customer, see below). Use the pair that matches the customer's language. Customers never see this text.

Three steps follow Dograh's roles, each with one job and an exit that depends only on what the caller says (the pattern of pipecat-flows
and LiveKit's AgentTask): the start node plays the greeting, takes the reason without asking about it, and asks the name; the agent node asks once
whether there is anything else; the end node closes. The global node holds persona, tone and rules, and leaves no slot for an acknowledgement:
the next question is the reply, and the persona never asks about the substance of the caller's matter, because the owner will. A text-chat
check with a simulated caller (12 calls, judged per reply) took restating from 46 of 54 replies to 13 of 40 and questions about the matter
from 16 to 0; the earlier wording (an acknowledgement slot, an open "ask what the owner needs" step) was the cause, not the model. Each of the
three extracts what it has so far, because a caller can hang up in any of them. A post-call QA node reviews every call. The prompts follow
OpenAI's voice prompting guide (role, tone and length, language, unclear audio, what never to do, hand-off).

Speech recognition mishears the business name unless it is given as a keyterm: on a recorded call Deepgram heard `영혼솔루션` without it and
`영훈솔루션` with it. Dograh reads the workflow field `dictionary` (comma-separated, once per workflow, no call variables), so set it to the
business name on each customer's copy of the workflow.

Both are an office front desk, not only a consultation line: the caller may be a new inquiry, an existing customer, a partner or
vendor, or a sales call. The greeting says it is an AI (Korea AI Framework Act Art. 31 and MSIT guideline, for a voice that can pass as human); the English
one also says the call is recorded (all-party-consent US states; a Korean recording notice is not required by 통신비밀보호법, only by PIPA if
the legal basis is consent; the customer's privacy policy must name the processors and the cross-border transfer). It takes the message, and promises nothing
about price, schedule, feasibility or the status of existing work. Sections follow OpenAI's voice prompting guide (role, tone,
unclear audio, escalation; one question at a time; vary wording). `consultation-ko.json` and `consultation-en.json` are the same product prompt in two languages; neither names a business or an
industry. The business name is the variable `{{business_name | fallback:...}}` in the greeting and the global prompt (verified: it renders
in both, and the fallback fills in when the value is missing). Dograh gives a production call no such value by itself (workflow-level
context variables apply to test calls only), so there are two ways to supply it. For the first customers, copy the workflow and replace
the expression with the literal name (`sed`, below). For many customers, set Pre-Call Data Fetch on the start node: Dograh POSTs the
called number to a Synchronize endpoint and merges the returned `initial_context` (`business_name`) into the call; that endpoint is not
built yet. Keep the name before a noun (`... AI 비서예요`), never before a Korean particle. A prompt must not contain `{{` or `}}` other than
that variable, nor `"`, `\`, `/` or `&`. Who the caller is goes in the first line of `facts` (`구분:` or
`Caller type:`), so the Synchronize page needs no extra field.

```bash
sed -i 's/{{business_name | fallback:[^}]*}}/Acme Plumbing/g' consultation-en.json
H='Content-Type: application/json'; K="X-API-Key: $DOGRAH_API_KEY"   # the customer organization's key
ID=$(curl -fsS -X POST "$DOGRAH_URL/api/v1/workflow/create/definition" -H "$K" -H "$H" -d @consultation-en.json | jq .id)
curl -fsS -X PUT  "$DOGRAH_URL/api/v1/workflow/$ID" -H "$K" -H "$H" -d @consultation-en-settings.json
curl -fsS -X POST "$DOGRAH_URL/api/v1/workflow/$ID/publish" -H "$K" -H "$H" -d '{}'
```

The Synchronize reader expects, in the run's `gathered_context`: `customer_name`, `topic`, `summary`, `facts` (one
`label: value` per line), `quotes` (one verbatim caller sentence per line), `follow_up`, `follow_up_label` (at most 8
characters), `needs_owner_follow_up` (boolean), and `mapped_call_disposition` of `follow_up` or `done` (anything else,
such as `user_hangup`, means the call was not classified). The extraction sits on both the Consultation and the Closing
node because the caller can hang up in either. Edge labels stay ASCII: Dograh names the LLM function from them.

The model configuration (STT / LLM / TTS) is separate: see `../model-configuration.md`.
