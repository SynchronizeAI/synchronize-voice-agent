# Consultation workflows

The AI phone consultation, ready to load: `consultation-ko.json` and `consultation-en.json` are the body of
`POST /api/v1/workflow/create/definition`; `*-settings.json` holds its settings (the two dispositions and the turn strategy; add `"dictionary": "<business name>"` per customer, see below): after loading the workflow, enter them in Dograh's workflow settings and publish it, or every call shows as needing follow-up. Use the pair that matches the customer's language. Customers never see this text.

Three steps, the shape Dograh's own guide gives (one phase per node, a specific edge condition per exit). The start node plays the greeting, takes the
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

Speech recognition mishears the business name unless it is given as a keyterm: on a recorded call Deepgram heard `영혼솔루션` without it and
`영훈솔루션` with it. Dograh reads the workflow field `dictionary` (comma-separated, once per workflow, no call variables), so set it to the
business name on each customer's copy of the workflow.

Both are an office front desk, not only a consultation line: the caller may be a new inquiry, an existing customer, a partner or
vendor, or a sales call. The greeting says it is an AI (Korea AI Framework Act Art. 31 and MSIT guideline, for a voice that can pass as human); the English
one also says the call is recorded (all-party-consent US states; a Korean recording notice is not required by 통신비밀보호법, only by PIPA if
the legal basis is consent; the customer's privacy policy must name the processors and the cross-border transfer). It takes the message, and promises nothing
about price, schedule, feasibility or the status of existing work. `consultation-ko.json` and `consultation-en.json` are the same product prompt in two languages; neither names a business or an
industry. The business name is the variable `{{business_name | fallback:...}}` in the greeting and the global prompt (verified: it renders
in both, and the fallback fills in when the value is missing). Dograh gives a production call no such value by itself (workflow-level
context variables apply to test calls only), so there are two ways to supply it. For the first customers, copy the workflow and replace
the expression with the literal name in Dograh's workflow editor. For many customers, set Pre-Call Data Fetch on the start node: Dograh POSTs the
called number to a Synchronize endpoint and merges the returned `initial_context` (`business_name`) into the call; that endpoint is not
built yet. Keep the name before a noun (`... AI 비서예요`), never before a Korean particle. A conversation node's prompt must not contain `{{` or `}}` other than
that variable. Who the caller is goes in the first line of `facts` (`구분:` or
`Caller type:`), so the Synchronize page needs no extra field.

A `mapped_call_disposition` other than `follow_up` or `done`, such as `user_hangup`, means the call was not classified.
Edge labels stay ASCII: Dograh names the LLM function from them.
