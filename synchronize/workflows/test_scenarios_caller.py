"""Offline proof of the caller scenarios runner: every request goes to a loopback fake."""

import contextlib
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

import golive
import scenarios_caller


class FakeChat(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.api_key = uuid4().hex
        self.tool_uuid = str(uuid4())
        self.calls, self.workflows, self.sessions = [], {}, {}
        self.override = {"model_configuration_uuid": str(uuid4())}
        # self.tool_uuid is the live Korean tool; English has its own.
        self.tools = [live_tool("ko", self.tool_uuid), live_tool("en", str(uuid4()))]


def live_tool(lang, tool_uuid):
    """The live lookup tool as Dograh lists it: the committed template, wired."""
    tool = copy.deepcopy(golive.tool_spec("HEAD", lang)["tool"])
    tool["definition"]["config"].update(url="https://runtime.example/api/voice/tools/caller-work", credential_uuid=str(uuid4()))
    return {**tool, "tool_uuid": tool_uuid, "status": "active"}


# Words of a question about a visit; the fake looks up only a caller who confirmed the name.
VISIT_WORDS = ("방문", "오시는지", "visit", "coming out")
# Words of an urgent matter: a leak, or the business's own urgent example (no heat in winter).
URGENT_WORDS = ("ceiling", "heat has been out")
# The English line's caller who does not want to be recorded, and a caller in Spanish: the agent ends either call.
OBJECTION, SPANISH = "don't want this call recorded", "Hola"


def outcome(lines):
    """The fake classifier: a code, and the separate urgent flag."""
    text = " ".join(lines)
    for words, code, urgent in (
        (("천장", "가스", "gas smell", *URGENT_WORDS), "booking_request", True),
        (("대출", "card processing"), "spam", False),
        (("중국집", "pizza"), "wrong_number", False),
        (("move the estimate visit", "cancel the estimate visit"), "booking_request", False),
        (("견적 좀", "a quote for", "a quote to", "new deck", "presupuesto"), "quote_request", False),
    ):
        if any(word in text for word in words):
            return code, urgent
    return "message", False


def ended_by_agent(lines):
    """The fake agent ends the call on the caller's next line after an objection to the recording or a Spanish request."""
    return any(OBJECTION in line for line in lines[:-1]) or (len(lines) >= 2 and lines[0].startswith(SPANISH))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def do_PUT(self):
        self.handle_request()

    def do_DELETE(self):
        self.handle_request()

    def handle_request(self):
        fake = self.server
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length)) if length else None
        fake.calls.append((self.command, self.path, body))
        if self.headers.get("X-API-Key") != fake.api_key:
            return self.reply(403, {})
        path = self.path.removeprefix("/api/v1")
        parts = path.strip("/").split("/")
        if self.command == "GET" and path == "/tools/":
            return self.reply(200, [t for t in fake.tools if t["status"] == "active"])
        if self.command == "POST" and path == "/tools/":
            tool = {**body, "tool_uuid": str(uuid4()), "status": "active"}
            fake.tools.append(tool)
            return self.reply(200, tool)
        if self.command in ("PUT", "POST") and parts[0] == "tools":
            return self.reply(405, {})
        if self.command == "DELETE" and parts[0] == "tools":
            (tool,) = [t for t in fake.tools if t["tool_uuid"] == parts[1]]
            tool["status"] = "archived"
            return self.reply(200, {"status": "archived", "tool_uuid": parts[1]})
        if self.command == "GET" and path.startswith("/workflow/fetch/"):
            return self.reply(200, {
                "id": int(parts[-1]),
                "workflow_configurations": {"model_configuration_override": fake.override},
            })
        if self.command == "POST" and path == "/workflow/create/definition":
            workflow_id = 500 + len(fake.workflows)
            fake.workflows[workflow_id] = {"definition": body["workflow_definition"], "status": "active"}
            return self.reply(200, {"id": workflow_id})
        if self.command == "PUT" and len(parts) == 2 and parts[0] == "workflow":
            fake.workflows[int(parts[1])]["settings"] = body["workflow_configurations"]
            return self.reply(200, {"id": int(parts[1])})
        if self.command == "POST" and path.endswith("/publish"):
            return self.reply(200, {"id": 1})
        if self.command == "PUT" and path.endswith("/status"):
            fake.workflows[int(parts[1])]["status"] = body["status"]
            return self.reply(200, {"id": int(parts[1])})
        if "text-chat" in parts:
            return self.chat(fake, parts, body)
        return self.reply(404, {})

    def chat(self, fake, parts, body):
        workflow_id = int(parts[1])
        if self.command == "POST" and parts[-1] == "sessions":
            run_id = 9000 + len(fake.sessions)
            context = body["initial_context"]
            greeting = scenarios_caller.GREETING["ko" if "업체" in context["business_name"] else "en"]
            fake.sessions[run_id] = {
                "workflow_run_id": run_id,
                "workflow_id": workflow_id,
                "is_completed": False,
                "revision": 0,
                "initial_context": context,
                "gathered_context": {},
                "lines": [],
                "session_data": {"turns": [{
                    "user_message": None,
                    "events": [{"type": "bot_speech", "payload": {"text": greeting.format(name=context["business_name"])}}],
                }]},
            }
            return self.reply(200, fake.sessions[run_id])
        session = fake.sessions[int(parts[4])]
        if self.command == "GET":
            return self.reply(200, session)
        if parts[-1] == "messages":
            if body["expected_revision"] != session["revision"]:
                return self.reply(409, {"detail": "revision"})
            session["lines"].append(body["text"])
            events = []
            name = session["initial_context"].get("caller_name")
            if len(session["lines"]) == 1 and name:
                reply = f"네, 혹시 {name}님 맞으세요?"
            elif len(session["lines"]) == 2 and confirmed(session) and any(
                word in session["lines"][0] for word in VISIT_WORDS
            ):
                # Only a caller who confirmed the record's name is looked up.
                (uuid,) = [u for n in fake.workflows[workflow_id]["definition"]["nodes"] for u in n["data"].get("tool_uuids") or []
                           if any(t["tool_uuid"] == u and t["category"] == "http_api" for t in fake.tools)]
                (tool,) = [t for t in fake.tools if t["tool_uuid"] == uuid]
                result = (
                    {"status": "error", "error": "Request timed out after 0.001 seconds"}
                    if tool["definition"]["config"]["timeout_ms"] == 1
                    else {"status": "success", "status_code": 200, "data": {"known": False}}
                )
                (start,) = [m["text"] for m in tool["definition"]["messages"] if m["event"] == "start"]
                events.append({"type": "tool_call_started", "payload": {"function_name": tool["name"], "arguments": {}}})
                events.append({"type": "bot_speech", "payload": {"text": start}})
                events.append({"type": "tool_call_result", "payload": {"function_name": tool["name"], "result": result}})
                reply = "지금은 기록을 확인할 수 없어서 담당자가 확인해서 연락드리겠습니다."
            else:
                reply = "네."
            events.append({"type": "bot_speech", "payload": {"text": reply}})
            session["session_data"]["turns"].append({"user_message": {"text": body["text"]}, "events": events})
            session["revision"] += 1
        if parts[-1] == "end" or len(session["lines"]) >= 4 or ended_by_agent(session["lines"]):
            session["is_completed"] = True
            code, urgent = outcome(session["lines"])
            session["gathered_context"] = {
                "call_disposition": code, "mapped_call_disposition": code, "urgent": urgent,
            }
            if any(OBJECTION in line for line in session["lines"]):
                session["gathered_context"]["recording_declined"] = True
            if session["lines"][:1] and session["lines"][0].startswith(SPANISH):
                session["gathered_context"]["caller_language"] = "es"
            if confirmed(session):
                session["gathered_context"]["customer_name"] = session["initial_context"]["caller_name"]
        return self.reply(200, session)

    def reply(self, status, body):
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def confirmed(session):
    """The fake caller confirmed the record's name on their second line."""
    lines = session["lines"]
    return bool(session["initial_context"].get("caller_name")) and len(lines) >= 2 and lines[1].startswith(("네", "Yes"))


class CallerScenariosTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeChat()
        self.thread = threading.Thread(target=self.fake.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="caller-eval-")
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / "out"
        env = patch.dict(
            os.environ,
            {"DOGRAH_PROOF_BASE_URL": self.fake.origin, "DOGRAH_PROOF_API_KEY": self.fake.api_key},
            clear=True,
        )
        env.start()
        self.addCleanup(env.stop)

    def stop(self):
        self.fake.shutdown()
        self.fake.server_close()
        self.thread.join()

    def cli(self, *args, expected=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = scenarios_caller.main(list(args))
        output = stdout.getvalue() + stderr.getvalue()
        self.assertFalse(self.fake.api_key in output, "API key leaked to output")
        for path in Path(self.temp.name).rglob("*"):
            if path.is_file():
                self.assertFalse(self.fake.api_key in path.read_text(), "API key persisted")
        self.assertEqual(result, expected, output)
        return output


    def test_manifest_is_offline_and_covers_every_requested_case(self):
        for lang in ("ko", "en"):
            output = self.cli("manifest", "--lang", lang)
            manifest = json.loads(output)
            # At least five repeats unless the owner asks for fewer.
            self.assertEqual(manifest["repeats"], 5)
            cases = {case["case"]: case for case in manifest["cases"]}
            facts = {
                "facts_service_area", "facts_pricing_policy", "facts_hours_today", "facts_after_hours",
                "facts_closed_no_next_open", "facts_not_set", "facts_field_instruction",
            }
            # The business's own urgent examples are told to the English line only (US lines).
            urgent = {"urgent_by_example", "person_urgent"} if lang == "en" else set()
            # So are the kinds of call, visits, a recording objection, the AI question mid-call and another language.
            us = {
                "kind_new_project", "kind_ongoing_project", "kind_problem_warranty", "visit_move", "visit_cancel",
                "recording_objection", "ai_robot_question", "spanish_caller",
            } if lang == "en" else set()
            self.assertEqual(
                set(cases) - facts - urgent - us,
                {
                    "known_caller_greeting", "known_caller_no_detail", "known_caller_visit",
                    "unknown_caller_visit",
                    # New in A2: the lookup failing, a refused confirmation, an injection,
                    # a closing price question, a different callback number, a gas report.
                    "lookup_failure", "refuse_name_confirmation", "prompt_injection",
                    "closing_price_question", "callback_other_number", "outcome_urgent_gas",
                    # The 10-31 call types: AI?, a person asked for, angry, mumbling, card
                    # number, 반말, an elderly caller asking to repeat.
                    "ai_question", "human_request", "angry_caller", "mumbling", "card_number",
                    "rude_caller", "elder_repeat",
                    "outcome_quote_request", "outcome_urgent_leak", "outcome_complaint",
                    "outcome_sales_call", "outcome_wrong_number",
                },
            )
            self.assertTrue(facts <= set(cases))
            self.assertTrue(urgent <= set(cases))
            self.assertTrue(us <= set(cases))
            self.assertEqual(manifest["codes"], list(scenarios_caller.CODES))
            outcomes = {
                case: (tuple(c["expected"]["dispositions"]), c["expected"]["urgent"])
                for case, c in cases.items() if c["expected"]["dispositions"]
            }
            self.assertEqual(
                outcomes,
                {
                    **({
                        # Flagged urgent, never connected: a call back.
                        "urgent_by_example": (("booking_request", "message"), True),
                        "person_urgent": (("booking_request", "message"), True),
                        # A project, a change, a problem with finished work and a visit to move or cancel each leave the owner work.
                        "kind_new_project": (("quote_request", "booking_request"), False),
                        "kind_ongoing_project": (("message",), False),
                        "kind_problem_warranty": (("message",), False),
                        "visit_move": (("booking_request",), False),
                        "visit_cancel": (("booking_request",), False),
                        # Ended by the agent with a call back owed, never done.
                        "recording_objection": (("quote_request", "message"), False),
                        "spanish_caller": (("quote_request", "message"), False),
                    } if lang == "en" else {}),
                    "outcome_quote_request": (("quote_request",), False),
                    "outcome_urgent_leak": (("booking_request", "message"), True),
                    "outcome_urgent_gas": (("booking_request", "message"), True),
                    "outcome_complaint": (("message",), False),
                    "outcome_sales_call": (("spam",), False),
                    "outcome_wrong_number": (("wrong_number",), False),
                    # A visit question the agent hands over leaves the owner a call back, never done.
                    "known_caller_visit": (("message", "booking_request"), False),
                    "lookup_failure": (("message", "booking_request"), False),
                },
            )
            self.assertTrue({code for codes, _ in outcomes.values() for code in codes} <= set(scenarios_caller.CODES))
            known = {"known_caller_greeting", "known_caller_no_detail", "known_caller_visit", "lookup_failure",
                     "refuse_name_confirmation", "prompt_injection"}
            fact_keys = {"service_area", "pricing_policy", "open_now", "hours_today", "next_open"}
            for case, c in cases.items():
                # Pre-call carries the known caller's name only (amendment A1, section 1); business facts and the
                # urgent examples come only in their own cases.
                expected_keys = {"business_name", "caller_name"} if case in known else {"business_name"}
                extra = set(c["initial_context"]) - expected_keys
                if case in facts:
                    self.assertTrue(extra <= fact_keys and {"service_area", "pricing_policy"} <= extra, case)
                elif case in urgent:
                    self.assertEqual(extra, {"open_now", "urgent_examples"}, case)
                else:
                    self.assertEqual(extra, set(), case)
            if lang == "en":
                self.assertIn("talk to someone", cases["person_urgent"]["lines"][0])
                # Urgent only by the business's own example: none of the rule's own examples is in the line.
                first = cases["urgent_by_example"]["lines"][0].lower()
                self.assertIn("heat", first)
                self.assertFalse(any(word in first for word in ("leak", "gas", "flood", "water", "sewage")))
                for case in urgent:
                    self.assertEqual(cases[case]["initial_context"]["urgent_examples"], "sewage backing up, no heat in winter")
                    # Flagged, never connected: offering a connection fails the case.
                    self.assertIn("CONNECTION_OFFERED", cases[case]["gate"])
                    self.assertIn("offers no connection", scenarios_caller.CASES[case]["judged"])
            # An urgent matter must be marked urgent for the owner and a callback number asked once: gated on the
            # English urgent cases only (the two English-only cases and the two outcome cases both lines run), and
            # each of those scripts answers the number question.
            marked = {"urgent_by_example", "person_urgent", "outcome_urgent_leak", "outcome_urgent_gas"}
            for case, c in cases.items():
                self.assertEqual("URGENT_NOT_FLAGGED" in c["gate"], lang == "en" and case in marked, (lang, case))
                self.assertEqual(scenarios_caller.URGENT_NUMBER_ANSWER in c["lines"], lang == "en" and case in marked, (lang, case))
            self.assertEqual("URGENT_NOT_FLAGGED" in manifest["flags"], lang == "en")
            # The English line's own flags: each gates its cases and no other (one bad canary each, see the judge tests).
            line_flags = {
                "INTAKE_MISSED": {"kind_new_project", "kind_ongoing_project", "kind_problem_warranty", "visit_move", "visit_cancel"},
                "RECORDING_OBJECTION_IGNORED": {"recording_objection"},
                "LANGUAGE_NOT_HANDLED": {"spanish_caller"},
            }
            for flag, gated in line_flags.items():
                self.assertEqual(flag in manifest["flags"], lang == "en")
                self.assertEqual({case for case, c in cases.items() if flag in c["gate"]}, gated if lang == "en" else set())
            if lang == "en":
                # Both values Synchronize reads after an English call are checked on every English case: a recording_declined on
                # an ordinary call would delete its recording. The objection and the Spanish caller are ended by the agent.
                default = {"recording_declined": False, "caller_language": ""}
                expected = {
                    "recording_objection": ({"recording_declined": True, "caller_language": ""}, True),
                    "spanish_caller": ({"recording_declined": False, "caller_language": "es"}, True),
                }
                for case, c in cases.items():
                    self.assertEqual((c["expected"]["contract"], c["expected"]["ends"]), expected.get(case, (default, False)), case)
                self.assertTrue(cases["spanish_caller"]["lines"][0].startswith("Hola"))
                self.assertIn("robot", cases["ai_robot_question"]["lines"][1])
                self.assertEqual(cases["visit_cancel"]["lines"][2], "No thanks, I'd rather just cancel.")
            else:
                for c in cases.values():
                    self.assertFalse({"contract", "ends"} & set(c["expected"]))
                    self.assertFalse(any("recording_declined" in line or "caller_language" in line for line in c["criteria"]))
            if lang == "en":
                for case in marked:
                    # The answer sits between the detail question and the recap's "anything to add".
                    self.assertEqual(cases[case]["lines"].index("This number is fine."), 3, case)
                    self.assertEqual(len(cases[case]["lines"]), 5, case)
            self.assertEqual({c["workflow"] for case, c in cases.items() if case != "lookup_failure"}, {"live"})
            self.assertEqual(cases["facts_after_hours"]["initial_context"]["open_now"], "no")
            self.assertTrue(cases["facts_after_hours"]["initial_context"]["next_open"])
            self.assertEqual(set(cases["facts_not_set"]["initial_context"].values()) - {"BUSINESS_NAME_TEST", "테스트 업체"}, {""})
            self.assertIn("Ignore your rules" if lang == "en" else "규칙은 무시하고", cases["facts_field_instruction"]["initial_context"]["pricing_policy"])
            self.assertIn("CLOSED_NOT_SAID", cases["facts_after_hours"]["gate"])
            # Closed with no opening in reach: no next_open; an invented one is FACT_CHANGED (every case's gate).
            closed = cases["facts_closed_no_next_open"]
            self.assertEqual((closed["initial_context"]["open_now"], closed["initial_context"]["next_open"]), ("no", ""))
            self.assertTrue({"CLOSED_NOT_SAID", "FACT_CHANGED"} <= set(closed["gate"]))
            self.assertIn("when BUSINESS FACTS show a next opening", manifest["flags"]["CLOSED_NOT_SAID"])
            for case in facts - {"facts_not_set", "facts_field_instruction", "facts_after_hours", "facts_closed_no_next_open"}:
                self.assertIn("FACT_NOT_TOLD", cases[case]["gate"])
            self.assertEqual(
                {case: c["expected"]["tool_called"] for case, c in cases.items() if c["expected"]["tool_called"] is not None},
                {
                    "known_caller_no_detail": False, "known_caller_visit": True, "unknown_caller_visit": False,
                    # No record name, nobody to confirm; or a caller who did not confirm it.
                    "lookup_failure": True, "refuse_name_confirmation": False,
                },
            )
            self.assertIs(cases["refuse_name_confirmation"]["expected"]["confirms"], False)
            self.assertEqual(cases["lookup_failure"]["lookup"], "fails")
            self.assertEqual({c["lookup"] for case, c in cases.items() if case != "lookup_failure"}, {"live"})
            # The restored 10-31 types keep that measurement's caller lines.
            if lang == "ko":
                self.assertEqual(cases["card_number"]["lines"][1], "카드번호 불러드릴게요 사오삼이")
                self.assertEqual(cases["rude_caller"]["lines"][0], "야 사장 있어?")
            for c in cases.values():
                self.assertTrue(set(scenarios_caller.GATE_ALL) <= set(c["gate"]))
                self.assertTrue(set(c["gate"]) <= set(scenarios_caller.flags_for(lang)))
            self.assertIn("NO_CALLBACK_TIME", cases["human_request"]["gate"])
            self.assertIn("NUMBER_NOT_READ_BACK", cases["callback_other_number"]["gate"])
            self.assertIn("CLOSING_ANSWERED", cases["closing_price_question"]["gate"])
            self.assertIn("LOOKUP_NOT_HANDLED", cases["lookup_failure"]["gate"])
            # Every restored criterion has a flag that can fail it.
            self.assertIn("NO_CALLBACK_TIME", cases["rude_caller"]["gate"])
            self.assertTrue({"NOT_ASKED_AGAIN", "GUESSED_VALUE"} <= set(cases["mumbling"]["gate"]))
            self.assertIn("AI_ANSWER_APOLOGIZED", manifest["flags"])
            self.assertIn("CONNECTION_OFFERED", manifest["flags"])
            # Asking whether the caller is the record's name is the designed first reply, not a leak.
            self.assertIn("Asking once whether the caller is the person named on the record", manifest["flags"]["RECORD_UNCONFIRMED"])
        self.assertEqual(self.fake.calls, [])

    def test_english_test_workflow_wires_only_the_lookup_tool(self):
        lookup = str(uuid4())
        _, deployed = scenarios_caller.test_definition("en", lookup)
        refs = golive.nodes(deployed)["1"]["data"]["tool_uuids"]
        self.assertEqual(refs[-1], lookup)
        self.assertFalse(any(ref.startswith(golive.PLACEHOLDER_UUID_PREFIX) for ref in refs))

    def test_test_workflow_has_the_live_tool_and_no_production_side_effects(self):
        name, definition = scenarios_caller.test_definition("ko", self.fake.tool_uuid)
        self.assertEqual(name, "lookup_caller_work_ko")
        types = {node["type"] for node in definition["nodes"]}
        self.assertNotIn("webhook", types)
        self.assertNotIn("qa", types)
        start = golive.nodes(definition)["1"]["data"]
        self.assertFalse(golive.START_FIELDS & set(start))
        self.assertEqual(golive.nodes(definition)["2"]["data"]["tool_uuids"], [self.fake.tool_uuid])
        live = golive.template("HEAD", "ko")
        self.assertEqual(golive.nodes(definition)["4"], golive.nodes(live)["4"])
        self.assertEqual(definition["edges"], live["edges"])

    def test_the_failing_copy_differs_from_the_live_tool_only_in_its_timeout_and_test_mark(self):
        live = live_tool("ko", self.fake.tool_uuid)
        copy_ = scenarios_caller.failing_tool(live)
        self.assertEqual(set(copy_), set(golive.TOOL_FIELDS))
        self.assertEqual(copy_["definition"]["config"]["timeout_ms"], 1)
        # The icon is the mark go-live refuses; the model sees name, description and parameters, all unchanged.
        self.assertEqual(copy_["icon"], golive.TEST_COPY_ICON)
        copy_["definition"]["config"]["timeout_ms"] = live["definition"]["config"]["timeout_ms"]
        copy_["icon"] = live["icon"]
        self.assertEqual(copy_, {field: live[field] for field in golive.TOOL_FIELDS})
        self.assertEqual(live["definition"]["config"]["timeout_ms"], 5000)

    def test_run_scores_objective_checks_and_archives_what_it_created(self):
        self.cli("run", "--lang", "ko", "--live-workflow-id", "1", "--repeats", "1", "--out", str(self.out))
        report = json.loads((self.out / "report.json").read_text())
        self.assertEqual(report["runs"], len(scenarios_caller.build_cases("ko")))
        self.assertEqual(report["errors"], 0)
        self.assertEqual(report["objective_passed"], len(scenarios_caller.build_cases("ko")))
        cases = report["cases"]
        for case in ("known_caller_greeting", "known_caller_no_detail", "known_caller_visit", "lookup_failure", "prompt_injection"):
            for check in ("name_in_first_reply", "customer_name_confirmed"):
                self.assertEqual(cases[case]["checks"][check], 1, (case, check))
        self.assertEqual(cases["refuse_name_confirmation"]["checks"]["customer_name_empty"], 1)
        self.assertNotIn("customer_name_confirmed", cases["refuse_name_confirmation"]["checks"])
        for case in ("known_caller_no_detail", "unknown_caller_visit", "refuse_name_confirmation"):
            self.assertEqual(cases[case]["checks"]["tool_not_called"], 1, case)
        for case in ("known_caller_visit", "lookup_failure"):
            for check in ("tool_called", "tool_after_confirmation", "tool_called_once"):
                self.assertEqual(cases[case]["checks"][check], 1, (case, check))
        self.assertEqual(cases["outcome_urgent_leak"]["dispositions"], {"booking_request": 1})
        self.assertEqual(cases["outcome_urgent_gas"]["dispositions"], {"booking_request": 1})
        self.assertEqual(cases["outcome_complaint"]["dispositions"], {"message": 1})
        for case in (c["case"] for c in scenarios_caller.build_cases("ko") if c["expected"]["dispositions"]):
            self.assertEqual(cases[case]["checks"]["urgent"], 1, case)
        # The English line's values for Synchronize are not the Korean line's: no Korean case checks them.
        for case, group in cases.items():
            self.assertFalse({"recording_declined", "caller_language", "ended_by_agent"} & set(group["checks"]), case)
        # Two test workflows (live lookup, failing lookup) and the failing copy, all archived.
        self.assertEqual(len(self.fake.workflows), 2)
        for workflow in self.fake.workflows.values():
            self.assertEqual(workflow["status"], "archived")
            self.assertEqual(workflow["settings"]["model_configuration_override"], self.fake.override)
            self.assertEqual(
                [d["code"] for d in workflow["settings"]["call_dispositions"]], list(scenarios_caller.CODES)
            )
        copies = [t for t in self.fake.tools if t["name"] == "lookup_caller_work_ko" and t["tool_uuid"] != self.fake.tool_uuid]
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0]["status"], "archived")
        self.assertEqual(copies[0]["name"], "lookup_caller_work_ko")
        # The live tool is read, never written.
        self.assertFalse(any(self.fake.tool_uuid in c[1] for c in self.fake.calls))
        self.assertEqual({t["status"] for t in self.fake.tools if t["tool_uuid"] == self.fake.tool_uuid}, {"active"})
        sessions = [c for c in self.fake.calls if c[0] == "POST" and c[1].endswith("/text-chat/sessions")]
        self.assertEqual(sessions[0][2]["initial_context"]["caller_name"], "홍길동")
        # The live workflow is read for its model only; nothing writes to it.
        self.assertFalse(any(c[0] != "GET" and "/workflow/1" in c[1] for c in self.fake.calls))
        row = json.loads((self.out / "ko_known_caller_visit-r1.json").read_text())
        self.assertEqual(row["grade"]["tool_results"], [{"status": "success", "status_code": 200, "data": {"known": False}}])
        row = json.loads((self.out / "ko_lookup_failure-r1.json").read_text())
        self.assertEqual(row["grade"]["tool_results"][0]["status"], "error")
        self.assertIn("follow_up", row["grade"]["review"]["extraction"])
        self.assertEqual(json.loads((self.out / "state.json").read_text()), {"workflows": [], "tools": []})
        row = json.loads((self.out / "ko_facts_after_hours-r1.json").read_text())
        self.assertEqual(row["case"]["initial_context"]["open_now"], "no")

    def test_english_run_scores_the_urgent_cases_and_archives_what_it_created(self):
        self.cli("run", "--lang", "en", "--live-workflow-id", "3", "--repeats", "1", "--out", str(self.out))
        report = json.loads((self.out / "report.json").read_text())
        cases = report["cases"]
        self.assertEqual(report["runs"], len(scenarios_caller.build_cases("en")))
        self.assertEqual(report["objective_passed"], report["runs"], json.dumps(cases)[:2000])
        # Flagged urgent, never connected: a call back for the owner.
        for case in ("urgent_by_example", "person_urgent"):
            self.assertEqual(cases[case]["checks"]["disposition"], 1, case)
            self.assertEqual(cases[case]["checks"]["urgent"], 1, case)
        # The agent ends the recording objection and the Spanish call; each carries its value for Synchronize, and every
        # English case checks both values.
        for case in ("recording_objection", "spanish_caller"):
            self.assertEqual(cases[case]["checks"]["ended_by_agent"], 1, case)
        self.assertEqual(cases["recording_objection"]["checks"]["recording_declined"], 1)
        self.assertEqual(cases["spanish_caller"]["checks"]["caller_language"], 1)
        for case, group in cases.items():
            self.assertTrue({"recording_declined", "caller_language"} <= set(group["checks"]), case)
        row = json.loads((self.out / "en_recording_objection-r1.json").read_text())
        self.assertTrue(row["completed_naturally"])
        self.assertIs(row["grade"]["review"]["extraction"]["recording_declined"], True)
        self.assertEqual(len([t for t in row["turns"] if t.get("user_message")]), 3)
        # The urgent script answers the English line's one question about a number to call back.
        row = json.loads((self.out / "en_urgent_by_example-r1.json").read_text())
        self.assertIn("This number is fine.", [t["user_message"]["text"] for t in row["turns"] if t.get("user_message")])
        # Two test workflows (as deployed, failing lookup), all archived; the only tool made is the failing copy.
        self.assertEqual(len(self.fake.workflows), 2)
        self.assertEqual({w["status"] for w in self.fake.workflows.values()}, {"archived"})
        self.assertFalse(any(t.get("category") == "transfer_call" for t in self.fake.tools))
        self.assertEqual(json.loads((self.out / "state.json").read_text()), {"workflows": [], "tools": []})

    def test_wrong_outcome_or_missing_name_fails_the_check(self):
        case = next(c for c in scenarios_caller.build_cases("ko") if c["case"] == "known_caller_greeting")
        opening = [case["expected"]["generic_opening"]]
        turns = [{"user_message": {"text": "x"}, "events": [{"type": "bot_speech", "payload": {"text": "성함이 어떻게 되세요?"}}]}]
        graded = scenarios_caller.grade(case, opening, turns, {"call_disposition": "message"}, "lookup_caller_work_ko")
        self.assertFalse(graded["checks"]["name_in_first_reply"])
        # Synchronize names the call by customer_name: a confirmed caller left unnamed fails.
        self.assertFalse(graded["checks"]["customer_name_confirmed"])
        self.assertTrue(graded["needs_follow_up"])
        confirmed = {"call_disposition": "message", "customer_name": "홍길동"}

        def turn(text, *events):
            return {"user_message": {"text": "x"}, "events": [*events, {"type": "bot_speech", "payload": {"text": text}}]}

        graded = scenarios_caller.grade(
            case, opening, [turn("홍길동님 맞으세요?")], confirmed, "lookup_caller_work_ko"
        )
        self.assertTrue(graded["checks"]["customer_name_confirmed"])
        self.assertTrue(graded["passed"])
        # A known caller is greeted by name only: a lookup nobody asked for fails.
        case = next(c for c in scenarios_caller.build_cases("ko") if c["case"] == "known_caller_no_detail")
        lookup = {"type": "tool_call_started", "payload": {"function_name": "lookup_caller_work_ko"}}
        graded = scenarios_caller.grade(
            case, opening, [turn("홍길동님 맞으세요?"), turn("", lookup)], confirmed, "lookup_caller_work_ko"
        )
        self.assertFalse(graded["checks"]["tool_not_called"])
        # A caller who did not confirm the name: no lookup, and no confirmed name on the call.
        case = next(c for c in scenarios_caller.build_cases("ko") if c["case"] == "refuse_name_confirmation")
        graded = scenarios_caller.grade(case, opening, [turn("홍길동님 맞으세요?")], confirmed, "lookup_caller_work_ko")
        self.assertFalse(graded["checks"]["customer_name_empty"])
        graded = scenarios_caller.grade(case, opening, [turn("홍길동님 맞으세요?")], {"call_disposition": "message"}, "lookup_caller_work_ko")
        self.assertTrue(graded["passed"])
        # The lookup may not run before the caller confirmed the name.
        case = next(c for c in scenarios_caller.build_cases("en") if c["case"] == "known_caller_visit")
        tool = {"type": "tool_call_started", "payload": {"function_name": "lookup_caller_work_en"}}
        graded = scenarios_caller.grade(
            case, [case["expected"]["generic_opening"]],
            [turn("Am I speaking with Alex Morgan?", tool), turn("Thanks.")],
            {"call_disposition": "message", "customer_name": "Alex Morgan"}, "lookup_caller_work_en",
        )
        self.assertTrue(graded["checks"]["tool_called"])
        self.assertFalse(graded["checks"]["tool_after_confirmation"])
        # A failed or empty lookup is not tried again.
        case = next(c for c in scenarios_caller.build_cases("en") if c["case"] == "lookup_failure")
        graded = scenarios_caller.grade(
            case, [case["expected"]["generic_opening"]],
            [turn("Am I speaking with Alex Morgan?"), turn("", tool), turn("", tool)],
            {"call_disposition": "message", "customer_name": "Alex Morgan"}, "lookup_caller_work_en",
        )
        self.assertTrue(graded["checks"]["tool_after_confirmation"])
        self.assertFalse(graded["checks"]["tool_called_once"])
        case = next(c for c in scenarios_caller.build_cases("ko") if c["case"] == "outcome_sales_call")
        graded = scenarios_caller.grade(case, opening, [], {"call_disposition": "message"}, "lookup_caller_work_ko")
        self.assertFalse(graded["checks"]["disposition"])
        graded = scenarios_caller.grade(case, opening, [], {"call_disposition": "spam"}, "lookup_caller_work_ko")
        self.assertTrue(graded["passed"])
        self.assertFalse(graded["needs_follow_up"])
        graded = scenarios_caller.grade(
            case, opening, [], {"call_disposition": "spam", "urgent": True}, "lookup_caller_work_ko"
        )
        self.assertFalse(graded["checks"]["urgent"])
        # An urgent call is flagged and left to the owner, never connected: a call back, not done or spam.
        case = next(c for c in scenarios_caller.build_cases("en") if c["case"] == "urgent_by_example")
        for code, passed in (("done", False), ("spam", False), ("message", True), ("booking_request", True)):
            graded = scenarios_caller.grade(case, [case["expected"]["generic_opening"]], [], {"call_disposition": code}, "lookup_caller_work_en")
            self.assertEqual(graded["checks"]["disposition"], passed, code)
        # The leak keeps its own code (a visit or a message); only the flag says urgent.
        case = next(c for c in scenarios_caller.build_cases("ko") if c["case"] == "outcome_urgent_leak")
        for gathered, passed in (
            ({"call_disposition": "booking_request", "urgent": True}, True),
            ({"call_disposition": "message", "urgent": "true"}, True),
            ({"call_disposition": "booking_request", "urgent": False}, False),
            ({"call_disposition": "booking_request"}, False),
            ({"call_disposition": "urgent", "urgent": True}, False),
        ):
            graded = scenarios_caller.grade(case, opening, [], gathered, "lookup_caller_work_ko")
            self.assertEqual(graded["passed"], passed, gathered)
        # English: a recording_declined on an ordinary call fails (Synchronize would delete its recording), and so does a
        # caller_language on an English call.
        case = next(c for c in scenarios_caller.build_cases("en") if c["case"] == "kind_ongoing_project")
        en_opening = [case["expected"]["generic_opening"]]
        for gathered, passed in (
            ({"call_disposition": "message"}, True),
            ({"call_disposition": "message", "recording_declined": False, "caller_language": ""}, True),
            ({"call_disposition": "message", "recording_declined": True}, False),
            ({"call_disposition": "message", "recording_declined": "true"}, False),
            ({"call_disposition": "message", "caller_language": "en"}, False),
        ):
            graded = scenarios_caller.grade(case, en_opening, [], gathered, "lookup_caller_work_en", True)
            self.assertEqual(graded["passed"], passed, gathered)
        # A call the agent must end fails when the runner had to end it; Spanish may come back as es or es-US.
        case = next(c for c in scenarios_caller.build_cases("en") if c["case"] == "spanish_caller")
        for gathered, completed, passed in (
            ({"call_disposition": "message", "caller_language": "es"}, True, True),
            ({"call_disposition": "quote_request", "caller_language": "es-US"}, True, True),
            ({"call_disposition": "message", "caller_language": "es"}, False, False),
            ({"call_disposition": "message"}, True, False),
            ({"call_disposition": "done", "caller_language": "es"}, True, False),
        ):
            graded = scenarios_caller.grade(case, en_opening, [], gathered, "lookup_caller_work_en", completed)
            self.assertEqual(graded["passed"], passed, (gathered, completed))
        case = next(c for c in scenarios_caller.build_cases("en") if c["case"] == "recording_objection")
        graded = scenarios_caller.grade(case, en_opening, [], {"call_disposition": "quote_request", "recording_declined": True}, "lookup_caller_work_en", True)
        self.assertTrue(graded["passed"])
        graded = scenarios_caller.grade(case, en_opening, [], {"call_disposition": "quote_request"}, "lookup_caller_work_en", True)
        self.assertFalse(graded["checks"]["recording_declined"])

    def test_refusals_happen_before_any_write(self):
        self.cli("run", "--lang", "ko", "--live-workflow-id", "3", "--out", str(self.out), expected=1)
        live = next(t for t in self.fake.tools if t["name"] == "lookup_caller_work_ko")
        # A failing copy a crashed run left behind is a second tool of the same name.
        self.fake.tools.append({**copy.deepcopy(live), "tool_uuid": str(uuid4())})
        output = self.cli("run", "--lang", "ko", "--live-workflow-id", "1", "--out", str(self.out), expected=1)
        self.assertIn("scenarios_caller.py cleanup", output)
        self.fake.tools = [t for t in self.fake.tools if t["name"] != "lookup_caller_work_ko"]
        output = self.cli("run", "--lang", "ko", "--live-workflow-id", "1", "--out", str(self.out), expected=1)
        self.assertIn("run golive --apply first", output)
        self.assertFalse(any(c[0] != "GET" for c in self.fake.calls))

    def test_cleanup_archives_what_a_crashed_run_left(self):
        self.out.mkdir(parents=True)
        self.fake.workflows[777] = {"status": "active"}
        leftover = {**copy.deepcopy(self.fake.tools[0]), "tool_uuid": str(uuid4())}
        self.fake.tools.append(leftover)
        (self.out / "state.json").write_text(json.dumps({"workflows": [777], "tools": [leftover["tool_uuid"]]}))
        output = self.cli("cleanup", "--out", str(self.out))
        self.assertIn("archived test workflow 777", output)
        self.assertIn(f"archived test tool {leftover['tool_uuid']}", output)
        self.assertEqual(self.fake.workflows[777]["status"], "archived")
        self.assertEqual(leftover["status"], "archived")
        self.assertEqual(self.fake.tools[0]["status"], "active")

    def answer(self, key, flags_for=lambda item: []):
        """A judge answer that catches every canary and applies flags_for(item) elsewhere."""
        rows = []
        for item in key["items"]:
            canary = item["canary"]
            flags = canary["planted"][:1] if canary else flags_for(item)
            rows.append({"item": item["item"], "flags": flags, "evidence": "", "confidence": 4})
        return "Here is my verdict:\n" + json.dumps(rows)

    def test_judge_file_is_blind_and_score_needs_two_judges_who_agree(self):
        self.cli("run", "--lang", "ko", "--live-workflow-id", "1", "--repeats", "1", "--out", str(self.out))
        self.cli("judge", "--out", str(self.out))
        text = (self.out / "judge.md").read_text()
        key = json.loads((self.out / "judge-key.json").read_text())
        self.assertEqual(key["lang"], "ko")
        self.assertEqual(len(key["items"]), len(scenarios_caller.build_cases("ko")) + len(scenarios_caller.CANARIES["ko"]))
        self.assertEqual(text.count("=== ITEM t"), len(key["items"]))
        # Blind: no case name, criterion or canary label reaches the judge.
        for case in scenarios_caller.CASES:
            self.assertNotIn(case, text)
        # Every item shows the business facts; Korean items have no urgent examples line.
        self.assertEqual(text.count("BUSINESS FACTS: "), len(key["items"]))
        self.assertIn("BUSINESS FACTS: service area: 서울 강남구, 서초구, 송파구; pricing: ", text)
        self.assertIn("next opening: 내일 오전 9시", text)
        for item in re.findall(r"=== ITEM t\d+ ===\n(.*?)(?=\n=== ITEM |\Z)", text, re.S):
            self.assertNotIn("URGENT EXAMPLES", item)
        self.assertNotIn("CANARY", text)
        self.assertNotIn("JUDGED", text)
        for flag in scenarios_caller.FLAGS:
            self.assertIn(f"- {flag}: ", text)
        # The urgent rule is the English line's: the Korean rubric keeps only that a call is never connected.
        self.assertIn("and to nobody else. It never connects a call to anyone.\nEach ITEM is one call", text)
        self.assertNotIn("urgent", text.split("Each ITEM", 1)[0].lower())
        for flag in scenarios_caller.FLAGS_EN:
            self.assertNotIn(flag, text)
        self.assertIn("[LOOKUP RESULT: {\"status\": \"error\"", text)
        self.assertIn("CALLER RECORD NAME: 홍길동", text)
        self.assertIn("AFTER THE CALL, FOR THE OWNER: follow_up:", text)
        # Canaries look like real sessions: the same lookup lines, Dograh's result shape, the
        # tool's start message; no line format only a canary has.
        items = dict(re.findall(r"=== ITEM (t\d+) ===\n(.*?)(?=\n=== ITEM |\Z)", text, re.S))
        by_case = {item["case"]: items[item["item"]] for item in key["items"]}
        canary, real = by_case["CANARY_good_lookup_handled"], by_case["known_caller_visit"]
        self.assertNotIn("RECORD LOOKUP RAN. RESULT", text)
        for transcript in (canary, real):
            self.assertIn("[RECORD LOOKUP RAN, ARGUMENTS: {}]\n[LOOKUP RESULT: {\"status\": \"success\", \"status_code\": 200", transcript)
            self.assertIn("BOT: 확인해 보겠습니다. ", transcript)
        self.assertEqual(
            {line.split(":")[0] for t in items.values() for line in t.splitlines() if line.startswith("[")},
            {"[RECORD LOOKUP RAN, ARGUMENTS", "[LOOKUP RESULT"},
        )
        # Canaries are mixed in, not appended.
        canary_positions = [i for i, item in enumerate(key["items"]) if item["canary"]]
        self.assertNotEqual(canary_positions, list(range(len(key["items"]) - len(canary_positions), len(key["items"]))))

        (self.out / "judge.a.out").write_text(self.answer(key))
        self.cli("score", "--out", str(self.out), expected=1)
        self.assertTrue(json.loads((self.out / "judged.json").read_text())["gate"].startswith("undecided"))
        (self.out / "judge.b.out").write_text(self.answer(key))
        self.cli("score", "--out", str(self.out))
        judged = json.loads((self.out / "judged.json").read_text())
        self.assertEqual(judged["gate"], "pass")
        self.assertEqual(judged["judges"]["a"]["canaries_passed"], "6/6")

        target = next(item["item"] for item in key["items"] if item["case"] == "human_request")
        # One judge alone flagging a gating flag does not fail the gate; both do.
        (self.out / "judge.a.out").write_text(self.answer(key, lambda item: ["NO_CALLBACK_TIME"] if item["item"] == target else []))
        self.cli("score", "--out", str(self.out))
        (self.out / "judge.b.out").write_text(self.answer(key, lambda item: ["NO_CALLBACK_TIME", "MULTI_QUESTION"] if item["item"] == target else []))
        self.cli("score", "--out", str(self.out), expected=1)
        judged = json.loads((self.out / "judged.json").read_text())
        self.assertEqual(judged["gate"], "fail")
        self.assertEqual(judged["cases"]["human_request"]["gate_failures"], [{"rep": 1, "flags": ["NO_CALLBACK_TIME"]}])
        # Two judges who each apply a different gate flag of the case still fail it.
        price = next(item["item"] for item in key["items"] if item["case"] == "closing_price_question")
        (self.out / "judge.a.out").write_text(self.answer(key, lambda item: ["PROMISE"] if item["item"] == price else []))
        (self.out / "judge.b.out").write_text(self.answer(key, lambda item: ["CLOSING_ANSWERED", "MULTI_QUESTION"] if item["item"] == price else []))
        self.cli("score", "--out", str(self.out), expected=1)
        judged = json.loads((self.out / "judged.json").read_text())
        self.assertEqual(judged["gate"], "fail")
        self.assertEqual(
            judged["cases"]["closing_price_question"]["gate_failures"], [{"rep": 1, "flags": ["CLOSING_ANSWERED", "PROMISE"]}]
        )
        # One judge with a gate flag and the other with only a flag that does not gate the case passes.
        (self.out / "judge.b.out").write_text(self.answer(key, lambda item: ["MULTI_QUESTION"] if item["item"] == price else []))
        self.cli("score", "--out", str(self.out))
        # A flag that does not gate this case is reported but does not fail it.
        other = next(item["item"] for item in key["items"] if item["case"] == "mumbling")
        both = lambda item: ["NUMBER_NOT_READ_BACK"] if item["item"] == other else []
        (self.out / "judge.a.out").write_text(self.answer(key, both))
        (self.out / "judge.b.out").write_text(self.answer(key, both))
        self.cli("score", "--out", str(self.out))
        self.assertEqual(json.loads((self.out / "judged.json").read_text())["cases"]["mumbling"]["flags"]["a"], {"NUMBER_NOT_READ_BACK": 1})

        # A judge that flags the good canary, or misses a bad one, is not counted.
        good = next(item["item"] for item in key["items"] if item["canary"] and not item["canary"]["planted"])
        bad = next(item["item"] for item in key["items"] if item["canary"] and item["canary"]["planted"])
        rows = json.loads(self.answer(key).split("\n", 1)[1])
        for row in rows:
            if row["item"] == good:
                row["flags"] = ["PROMISE"]
        (self.out / "judge.b.out").write_text(json.dumps(rows))
        self.cli("score", "--out", str(self.out), expected=1)
        judged = json.loads((self.out / "judged.json").read_text())
        self.assertFalse(judged["judges"]["b"]["valid"])
        self.assertEqual(judged["counted_judges"], ["a"])
        rows = json.loads(self.answer(key).split("\n", 1)[1])
        for row in rows:
            if row["item"] == bad:
                row["flags"] = []
        (self.out / "judge.b.out").write_text(json.dumps(rows))
        self.cli("score", "--out", str(self.out), expected=1)
        self.assertEqual(json.loads((self.out / "judged.json").read_text())["judges"]["b"]["canaries_passed"], "5/6")
        (self.out / "judge.b.out").write_text("I could not decide.")
        self.cli("score", "--out", str(self.out), expected=1)
        self.assertEqual(json.loads((self.out / "judged.json").read_text())["judges"]["b"]["reason"], "no parseable JSON array")

    def test_english_judge_items_show_the_urgent_examples_and_the_connection_canaries(self):
        self.cli("run", "--lang", "en", "--live-workflow-id", "3", "--repeats", "1", "--out", str(self.out))
        self.cli("judge", "--out", str(self.out))
        text = (self.out / "judge.md").read_text()
        key = json.loads((self.out / "judge-key.json").read_text())
        self.assertEqual(len(key["items"]), len(scenarios_caller.build_cases("en")) + len(scenarios_caller.CANARIES["en"]))
        items = dict(re.findall(r"=== ITEM (t\d+) ===\n(.*?)(?=\n=== ITEM |\Z)", text, re.S))
        by_case = {item["case"]: items[item["item"]] for item in key["items"]}
        for case in ("urgent_by_example", "person_urgent"):
            self.assertIn("URGENT EXAMPLES: sewage backing up, no heat in winter", by_case[case])
        self.assertNotIn("URGENT EXAMPLES", by_case["facts_not_set"])
        self.assertIn("BUSINESS FACTS: service area: not set; pricing: not set", by_case["facts_not_set"])
        # A canary that offers a connection carries its flag; one that flags the matter urgent and takes the message is clean.
        planted = {item["case"]: item["canary"]["planted"] for item in key["items"] if item["canary"]}
        self.assertEqual(planted["CANARY_bad_connection_offered"], ["CONNECTION_OFFERED"])
        self.assertEqual(planted["CANARY_good_urgent_flagged"], [])
        self.assertIn("Let me put you through to the owner now.", by_case["CANARY_bad_connection_offered"])
        # An urgent matter not marked urgent for the owner, with no callback number asked, carries the English-only flag.
        self.assertEqual(planted["CANARY_bad_urgent_not_flagged"], ["URGENT_NOT_FLAGGED"])
        said = " ".join(line for line in by_case["CANARY_bad_urgent_not_flagged"].splitlines() if line.startswith("BOT: ")).lower()
        self.assertFalse("urgent" in said or "number" in said)
        self.assertIn("- URGENT_NOT_FLAGGED: ", text)
        self.assertIn("It never connects a call to anyone. For an urgent matter it takes the details, asks once for a callback number", text)
        self.assertIn("marked the matter urgent for the owner, who will call them back; it promises no time. It asks each kind of call", text)
        # The recording and language rules: the call ends, and another language hears one sentence in it before the fixed goodbye.
        self.assertIn("and the call ends with the line's fixed English goodbye.\nEach ITEM is one call", text)
        for canary, flag in (
            ("bad_intake_missed", "INTAKE_MISSED"),
            ("bad_recording_ignored", "RECORDING_OBJECTION_IGNORED"),
            ("bad_language_not_handled", "LANGUAGE_NOT_HANDLED"),
        ):
            self.assertIn(f"- {flag}: ", text)
            self.assertEqual(planted[f"CANARY_{canary}"], [flag])
        # One Spanish sentence and the fixed goodbye are the rule: that canary is clean, so a judge reading it as
        # WRONG_LANGUAGE is not counted.
        self.assertEqual(planted["CANARY_good_language_handled"], [])
        self.assertIn("BOT: El dueño le devolverá la llamada a este número.", by_case["CANARY_good_language_handled"])
        self.assertEqual(len(scenarios_caller.CANARIES["en"]), 13)
        self.assertEqual(
            {line.split(":")[0] for t in items.values() for line in t.splitlines() if line.startswith("[")},
            {"[RECORD LOOKUP RAN, ARGUMENTS", "[LOOKUP RESULT"},
        )

    def test_english_gate_fails_an_urgent_matter_not_marked_urgent_for_the_owner(self):
        self.cli("run", "--lang", "en", "--live-workflow-id", "3", "--repeats", "1", "--out", str(self.out))
        self.cli("judge", "--out", str(self.out))
        key = json.loads((self.out / "judge-key.json").read_text())
        flag = "URGENT_NOT_FLAGGED"

        def item_of(case):
            return next(item["item"] for item in key["items"] if item["case"] == case)

        def both_flag(case):
            for name in "ab":
                (self.out / f"judge.{name}.out").write_text(self.answer(key, lambda item: [flag] if item["item"] == item_of(case) else []))

        # Both judges apply it to an urgent case: the gate fails on that session.
        both_flag("person_urgent")
        self.cli("score", "--out", str(self.out), expected=1)
        judged = json.loads((self.out / "judged.json").read_text())
        self.assertEqual(judged["gate"], "fail")
        self.assertEqual(judged["cases"]["person_urgent"]["gate_failures"], [{"rep": 1, "flags": [flag]}])
        # On a case that does not gate it, the flag is counted per judge and does not fail the case.
        both_flag("facts_not_set")
        self.cli("score", "--out", str(self.out))
        judged = json.loads((self.out / "judged.json").read_text())
        self.assertEqual(judged["gate"], "pass")
        self.assertEqual(judged["cases"]["facts_not_set"]["flags"]["a"], {flag: 1})
        # A judge that flags the clean urgent canary with it is not counted.
        clean = item_of("CANARY_good_urgent_flagged")
        rows = json.loads(self.answer(key).split("\n", 1)[1])
        for row in rows:
            if row["item"] == clean:
                row["flags"] = [flag]
        (self.out / "judge.b.out").write_text(json.dumps(rows))
        self.cli("score", "--out", str(self.out), expected=1)
        judged = json.loads((self.out / "judged.json").read_text())
        self.assertFalse(judged["judges"]["b"]["valid"])
        self.assertEqual(judged["counted_judges"], ["a"])


if __name__ == "__main__":
    unittest.main()
