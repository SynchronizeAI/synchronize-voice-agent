"""Caller memory, record lookup and call-outcome scenarios through Dograh's text-chat API.

manifest prints every case and its pass criteria, offline. run is for the owner: it reads
DOGRAH_PROOF_BASE_URL / DOGRAH_PROOF_API_KEY from the environment, builds one isolated test
workflow per run from this checkout's committed templates (HEAD), runs every case, writes the
transcripts and a report, and archives the test workflow. It never routes a number, never edits a
live workflow (the live one is only read for its model configuration) and prints no connection value.
cleanup archives the test workflows a crashed run left behind (ids are in the run's state.json).
"""

import argparse
import copy
import json
from pathlib import Path
import sys
import time
import unicodedata

import golive

CODES = ("quote_request", "booking_request", "message", "done", "spam", "wrong_number")
# Contract: outcomes that leave the owner nothing to do. Everything else needs follow-up.
NO_FOLLOW_UP = ("done", "spam", "wrong_number")
# Pre-call gives a known caller's name only; open work comes from the in-call lookup alone.
KNOWN = {
    "ko": {"business_name": "테스트 업체", "caller_name": "홍길동"},
    "en": {"business_name": "BUSINESS_NAME_TEST", "caller_name": "Alex Morgan"},
}
GREETING = {
    "ko": "안녕하세요, {name} AI 비서입니다. 통화 내용은 녹음되어 담당자에게 전달됩니다.\n어떤 일로 전화하셨나요?",
    "en": "Thank you for calling {name}. I'm an AI assistant, and this call is recorded.\nHow may I help you?",
}
LINES = {
    "ko": {
        "known_caller_greeting": ["지난번에 견적 문의했던 건으로 전화드렸어요.", "네, 맞아요.", "견적서 언제쯤 받을 수 있는지 궁금해서요.", "아니요, 그게 다예요."],
        "known_caller_no_detail": ["여쭤볼 게 있어서 전화드렸어요.", "네, 맞아요.", "욕실 타일 하나가 깨졌는데 바꾸는 데 얼마나 드는지 궁금해요.", "아니요, 그게 다예요."],
        "known_caller_quote_status": ["견적 어떻게 됐어요?", "네, 맞아요.", "네, 알겠습니다.", "아니요, 그게 다예요."],
        "unknown_caller_quote_status": ["견적 어떻게 됐어요?", "김서연이요.", "네, 알겠습니다.", "아니요, 그게 다예요."],
        "outcome_quote_request": ["거실 도배 견적 좀 받아 보고 싶어서요.", "김서연이요.", "32평 아파트 거실이에요.", "아니요, 그게 다예요."],
        "outcome_urgent_leak": ["지금 욕실 천장에서 물이 계속 새고 있어요.", "김서연이요.", "아래층까지 물이 떨어질 것 같아요, 빨리 좀 봐 주세요.", "네, 부탁드려요."],
        "outcome_complaint": ["지난주에 해 주신 도배가 벌써 들뜨기 시작했어요, 너무 실망스럽네요.", "김서연이요.", "거실 벽 이음새 쪽이 다 일어났어요.", "아니요, 연락이나 빨리 주세요."],
        "outcome_sales_call": ["안녕하세요, 사업자 대출 상품 안내드리려고 연락드렸습니다.", "저는 이민호 팀장입니다.", "대표님께 금리 좋은 상품을 소개해 드리고 싶어서요.", "네, 그럼 다음에 다시 연락드릴게요."],
        "outcome_wrong_number": ["거기 중국집 아니에요?", "아, 죄송합니다. 잘못 걸었네요."],
    },
    "en": {
        "known_caller_greeting": ["Hi, I'm calling about the quote I asked for last time.", "Yes, that's me.", "I was wondering when I'd get the estimate.", "No, that's all."],
        "known_caller_no_detail": ["Hi, I'm calling with a question.", "Yes, that's me.", "One of my bathroom tiles cracked and I'd like to know what replacing it costs.", "No, that's all."],
        "known_caller_quote_status": ["How's my quote coming along?", "Yes, that's me.", "Okay, thanks.", "No, that's all."],
        "unknown_caller_quote_status": ["How's my quote coming along?", "Jamie Lee.", "Okay, thanks.", "No, that's all."],
        "outcome_quote_request": ["Hi, I'd like a quote for painting my living room.", "Jamie Lee.", "It's about four hundred square feet.", "No, that's all."],
        "outcome_urgent_leak": ["Water is pouring through my bathroom ceiling right now.", "Jamie Lee.", "It's dripping onto the floor below, please come as soon as you can.", "Yes, please hurry."],
        "outcome_complaint": ["The wallpaper you put up last week is already peeling, I'm really disappointed.", "Jamie Lee.", "It's all along the seams in the living room.", "No, just call me back soon."],
        "outcome_sales_call": ["Hi, I'm calling to offer your business a lower rate on card processing.", "This is Chris from Rate Partners.", "I'd love to tell the owner about our new plan.", "Okay, I'll try again later."],
        "outcome_wrong_number": ["Is this the pizza place?", "Oh, sorry, wrong number."],
    },
}
# The codes each outcome case may be classified as, and its urgent flag. Urgency is its own
# flag, so a leak is whatever the caller asked for (a visit, or a message) with urgent true.
EXPECTED_OUTCOME = {
    "outcome_quote_request": (("quote_request",), False),
    "outcome_urgent_leak": (("booking_request", "message"), True),
    "outcome_complaint": (("message",), False),
    "outcome_sales_call": (("spam",), False),
    "outcome_wrong_number": (("wrong_number",), False),
}
KNOWN_CRITERIA = [
    "OBJECTIVE: the agent's first reply uses the caller's name from caller_name (to confirm it, not to ask for it).",
    "OBJECTIVE: gathered_context.customer_name is the confirmed caller_name (Synchronize names the call by it).",
]
CRITERIA = {
    "known_caller_greeting": [
        "OBJECTIVE: the opening is the unchanged greeting (the name cannot be in the canned greeting; see README).",
        *KNOWN_CRITERIA,
        "JUDGED: it does not ask for the name again after the caller confirmed it, and promises nothing about the open quote.",
    ],
    "known_caller_no_detail": [
        *KNOWN_CRITERIA,
        "OBJECTIVE: the lookup tool is not called (the caller never asks about existing work; a known caller is greeted by name only).",
        "JUDGED: after the confirmation the agent asks its one open question and names no open matter, count or date.",
    ],
    "known_caller_quote_status": [
        *KNOWN_CRITERIA,
        "OBJECTIVE: the lookup tool is called, first on the caller's confirmation line or later, never before it.",
        "JUDGED: the reply after the tool result says only what the result holds (a text-chat run is not an inbound call, so "
        "Synchronize answers known=false and the right reply is that the owner will check) and promises no price, date or feasibility.",
    ],
    "unknown_caller_quote_status": [
        "OBJECTIVE: the opening is today's generic greeting with the business name.",
        "JUDGED: the agent asks for the caller's name (no caller record), states no status from memory and promises nothing.",
    ],
}
# Whether the lookup must run (True) or must not (False); None: either is fine.
TOOL_CALLED = {"known_caller_quote_status": True, "known_caller_no_detail": False}
for _case, (_codes, _urgent) in EXPECTED_OUTCOME.items():
    CRITERIA[_case] = [
        f"OBJECTIVE: the call is classified {' or '.join(_codes)} (gathered_context.call_disposition).",
        f"OBJECTIVE: gathered_context.urgent is {'true' if _urgent else 'not true'}.",
    ]


def build_cases(lang):
    cases = []
    for case_id, lines in LINES[lang].items():
        if case_id.startswith("known_caller"):
            context = dict(KNOWN[lang])
        else:
            context = {"business_name": KNOWN[lang]["business_name"]}
        cases.append(
            {
                "id": f"{lang}_{case_id}",
                "case": case_id,
                "initial_context": context,
                "lines": lines,
                "expected": {
                    "generic_opening": GREETING[lang].format(name=context["business_name"]),
                    "caller_name": context.get("caller_name"),
                    "tool_called": TOOL_CALLED.get(case_id),
                    "dispositions": EXPECTED_OUTCOME.get(case_id, (None, None))[0],
                    "urgent": EXPECTED_OUTCOME.get(case_id, (None, None))[1],
                },
                "criteria": CRITERIA[case_id],
            }
        )
    return cases


def test_definition(lang, tool_uuid):
    """This checkout's template for an isolated test workflow.

    The lookup tool is the live one (golive creates it); the pre-call fetch, the completion
    webhook and the QA review are removed: none of them changes the conversation or the
    outcome, and the webhook would post test runs to the production runtime.
    """
    spec = golive.tool_spec("HEAD", lang)
    golive.require(spec is not None, "This checkout has no caller-work tool template")
    definition = golive.resolve_tool_refs(
        golive.template("HEAD", lang), spec["placeholder_uuid"], tool_uuid
    )
    definition["nodes"] = [n for n in definition["nodes"] if n["type"] not in ("webhook", "qa")]
    for node in definition["nodes"]:
        for field in golive.START_FIELDS:
            node["data"].pop(field, None)
    text = json.dumps(definition)
    golive.require(
        ".invalid" not in text and "<DOGRAH" not in text and golive.PLACEHOLDER_UUID_PREFIX not in text,
        "Unresolved placeholder in the test workflow",
    )
    return spec["tool"]["name"], definition


def assistant_texts(turns):
    out = []
    for turn in turns:
        spoken = [
            (event.get("payload") or {}).get("text") or ""
            for event in turn.get("events") or []
            if event.get("type") == "bot_speech"
        ]
        text = " ".join(t for t in spoken if t) or ((turn.get("assistant_message") or {}).get("text") or "")
        out.append(text.strip())
    return out


def normal(text):
    return " ".join(unicodedata.normalize("NFC", text or "").split())


def grade(case, opening, turns, gathered, tool_name):
    expected = case["expected"]
    # The opening turn has no caller message; replies answer the caller's lines, in order.
    caller_turns = [turn for turn in turns if turn.get("user_message")]
    replies = assistant_texts(caller_turns)
    first_reply = next((text for text in replies if text), "")

    def started(event):
        return event.get("type") == "tool_call_started" and (event.get("payload") or {}).get("function_name") == tool_name

    events = [event for turn in turns for event in turn.get("events") or []]
    tool_calls = [e for e in events if started(e)]
    # Index of the caller line on which the tool first ran; line 1 (0-based) confirms the name.
    tool_turns = [i for i, turn in enumerate(caller_turns) if any(started(e) for e in turn.get("events") or [])]
    results = [
        (e.get("payload") or {}).get("result")
        for e in events
        if e.get("type") == "tool_call_result" and (e.get("payload") or {}).get("function_name") == tool_name
    ]
    code = (gathered or {}).get("call_disposition")
    checks = {"opening_generic": normal(" ".join(opening)) == normal(expected["generic_opening"])}
    if expected["caller_name"]:
        checks["name_in_first_reply"] = expected["caller_name"] in first_reply
        checks["customer_name_confirmed"] = (
            normal((gathered or {}).get("customer_name")) == normal(expected["caller_name"])
        )
    if expected["tool_called"] is True:
        checks["tool_called"] = bool(tool_calls)
        checks["tool_after_confirmation"] = bool(tool_turns) and tool_turns[0] >= 1
    if expected["tool_called"] is False:
        checks["tool_not_called"] = not tool_calls
    if expected["dispositions"]:
        checks["disposition"] = code in expected["dispositions"]
        # A boolean extraction can come back as JSON true or as the text "true".
        checks["urgent"] = (str((gathered or {}).get("urgent")).lower() == "true") == expected["urgent"]
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "call_disposition": code,
        "mapped_call_disposition": (gathered or {}).get("mapped_call_disposition"),
        "urgent": (gathered or {}).get("urgent"),
        "needs_follow_up": code not in NO_FOLLOW_UP,
        "tool_results": results,
        "review": {"opening": opening, "replies": replies},
    }


class Session:
    def __init__(self, client):
        self.client = client

    def run(self, workflow_id, name, case, settle_seconds=60):
        base = f"/workflow/{workflow_id}/text-chat/sessions"
        session = self.client.api(
            "POST", base, {"name": name, "initial_context": case["initial_context"]}
        )
        run_id = session["workflow_run_id"]
        opening = assistant_texts(session["session_data"]["turns"])
        for line in case["lines"]:
            if session["is_completed"]:
                break
            body = {"text": line, "expected_revision": session["revision"]}
            try:
                session = self.client.api("POST", f"{base}/{run_id}/messages", body)
            except golive.GoLiveError as error:
                if "HTTP 409" not in str(error):
                    raise
                session = self.client.api("GET", f"{base}/{run_id}")
                body["expected_revision"] = session["revision"]
                session = self.client.api("POST", f"{base}/{run_id}/messages", body)
        forced = not session["is_completed"]
        if forced:
            session = self.client.api(
                "POST", f"{base}/{run_id}/end", {"expected_revision": session["revision"]}
            )
        # The outcome is classified as the session ends; read it back for a bounded time.
        deadline = time.monotonic() + settle_seconds
        while "call_disposition" not in (session.get("gathered_context") or {}):
            if time.monotonic() > deadline:
                break
            time.sleep(3)
            session = self.client.api("GET", f"{base}/{run_id}")
        turns = session["session_data"]["turns"]
        return {
            "run_id": run_id,
            "completed_naturally": not forced,
            "opening": opening,
            "turns": turns,
            "gathered_context": session.get("gathered_context"),
        }


def create_test_workflow(client, lang, live_workflow_id, state, out):
    tools = [t for t in client.api("GET", "/tools/") if t["name"] == f"lookup_caller_work_{lang}"]
    golive.require(len(tools) == 1, f"Run golive --apply first: need exactly one lookup_caller_work_{lang} tool")
    tool_name, definition = test_definition(lang, tools[0]["tool_uuid"])
    live = client.api("GET", f"/workflow/fetch/{live_workflow_id}")
    settings = copy.deepcopy(golive.settings_template("HEAD", lang))
    override = (live.get("workflow_configurations") or {}).get("model_configuration_override")
    if override:
        settings["model_configuration_override"] = override
    name = f"caller-eval-{lang}-{time.strftime('%Y%m%dT%H%M%S')}"
    created = client.api("POST", "/workflow/create/definition", {"name": name, "workflow_definition": definition})
    state.setdefault("workflows", []).append(created["id"])
    (out / "state.json").write_text(json.dumps(state, indent=1) + "\n")
    client.api("PUT", f"/workflow/{created['id']}", {"workflow_configurations": settings})
    client.api("POST", f"/workflow/{created['id']}/publish", {})
    return created["id"], tool_name


def archive(client, state, out):
    for workflow_id in list(state.get("workflows", [])):
        client.api("PUT", f"/workflow/{workflow_id}/status", {"status": "archived"})
        state["workflows"].remove(workflow_id)
        (out / "state.json").write_text(json.dumps(state, indent=1) + "\n")
        print(f"archived test workflow {workflow_id}")


def report(rows):
    groups = {}
    for row in rows:
        group = groups.setdefault(row["case"]["case"], {"runs": 0, "passed": 0, "checks": {}, "dispositions": {}})
        group["runs"] += 1
        group["passed"] += bool(row["grade"]["passed"])
        for check, ok in row["grade"]["checks"].items():
            group["checks"][check] = group["checks"].get(check, 0) + bool(ok)
        code = row["grade"]["call_disposition"] or "none"
        group["dispositions"][code] = group["dispositions"].get(code, 0) + 1
    return {
        "runs": len(rows),
        "objective_passed": sum(bool(row["grade"]["passed"]) for row in rows),
        "errors": sum(bool(row.get("error")) for row in rows),
        "cases": groups,
        "judged": "Read every review block: the JUDGED criteria in manifest are not scored here.",
    }


def run(args):
    client = golive.Dograh()
    args.out.mkdir(parents=True, exist_ok=True)
    state = {}
    rows = []
    try:
        workflow_id, tool_name = create_test_workflow(client, args.lang, args.live_workflow_id, state, args.out)
        session = Session(client)
        for case in build_cases(args.lang):
            for rep in range(1, args.repeats + 1):
                row = {"case": case, "rep": rep, "workflow_id": workflow_id}
                try:
                    result = session.run(workflow_id, f"caller-eval-{case['id']}-r{rep}", case)
                    row.update(result, grade=grade(case, result["opening"], result["turns"], result["gathered_context"], tool_name))
                except golive.GoLiveError as error:
                    row.update(error=str(error), grade={"passed": False, "checks": {}, "call_disposition": None})
                rows.append(row)
                (args.out / f"{case['id']}-r{rep}.json").write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n")
                print(case["id"], rep, row.get("error") or row["grade"]["checks"])
    finally:
        archive(client, state, args.out)
    summary = report(rows)
    (args.out / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("runs", "objective_passed", "errors")}))
    return 0 if summary["errors"] == 0 else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("manifest", "run", "cleanup"), default="manifest")
    parser.add_argument("--lang", choices=("ko", "en"), default="ko")
    parser.add_argument("--live-workflow-id", type=int, help="read only, for its model configuration")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "manifest":
            print(json.dumps({"codes": CODES, "cases": build_cases(args.lang)}, ensure_ascii=False, indent=2))
            return 0
        golive.require(args.out is not None, "--out is required")
        if args.command == "cleanup":
            path = args.out / "state.json"
            state = json.loads(path.read_text()) if path.is_file() else {}
            archive(golive.Dograh(), state, args.out)
            return 0
        golive.require(args.repeats >= 1, "--repeats must be positive")
        golive.require(
            golive.TARGETS.get(args.live_workflow_id) == args.lang,
            "--live-workflow-id must be 1 for ko or 3 for en",
        )
        return run(args)
    except golive.GoLiveError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
