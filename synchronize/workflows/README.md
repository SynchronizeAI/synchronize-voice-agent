# Consultation workflows

The AI phone consultation, ready to load: `consultation-ko.json` and `consultation-en.json` are the body of
`POST /api/v1/workflow/create/definition`; `*-settings.json` is the body of `PUT /api/v1/workflow/{id}` (call limit
900 s and the two dispositions). Use the pair that matches the customer's language. Customers never see this text.

Both are an office front desk, not only a consultation line: the caller may be a new inquiry, an existing customer, a partner or
vendor, or a sales call. The AI says it is an AI and that the call is recorded in its greeting, takes the message, and promises nothing
about price, schedule, feasibility or the status of existing work. Sections follow OpenAI's voice prompting guide (role, tone,
unclear audio, escalation; one question at a time; vary wording). `consultation-ko.json` is 영훈솔루션's line;
`consultation-en.json` is generic: replace every `YOUR BUSINESS NAME` (workflow name, greeting, persona, closing) with the business
name. A prompt must not contain `{{`, `}}`, `"`, `\`, `/` or `&`. Who the caller is goes in the first line of `facts` (`구분:` or
`Caller type:`), so the Synchronize page needs no extra field.

```bash
sed -i 's/YOUR BUSINESS NAME/Acme Plumbing/g' consultation-en.json
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
