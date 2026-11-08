"""Offline proof of the caller scenarios runner: every request goes to a loopback fake."""

import contextlib
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
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
        self.tools = [
            {"tool_uuid": self.tool_uuid, "name": f"lookup_caller_work_{lang}", "status": "active"}
            for lang in ("ko", "en")
        ]


def outcome(lines):
    """The fake classifier: a code, and the separate urgent flag."""
    text = " ".join(lines)
    for words, code in (
        (("천장", "ceiling"), "booking_request"),
        (("대출", "card processing"), "spam"),
        (("중국집", "pizza"), "wrong_number"),
        (("견적 좀", "a quote for"), "quote_request"),
    ):
        if any(word in text for word in words):
            return code, code == "booking_request"
    return "message", False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def do_PUT(self):
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
            return self.reply(200, fake.tools)
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
            elif len(session["lines"]) == 2 and name and (
                "견적 어떻게" in session["lines"][0] or "How's my quote" in session["lines"][0]
            ):
                # Only a caller who confirmed the record's name is looked up.
                tool = f"lookup_caller_work_{'ko' if '견적' in session['lines'][0] else 'en'}"
                events.append({"type": "tool_call_started", "payload": {"function_name": tool}})
                events.append({"type": "tool_call_result", "payload": {"function_name": tool, "result": {"status": "success", "data": {"known": False}}}})
                reply = "기록에서 찾지 못해 담당자가 확인해서 연락드리겠습니다."
            else:
                reply = "네."
            events.append({"type": "bot_speech", "payload": {"text": reply}})
            session["session_data"]["turns"].append({"user_message": {"text": body["text"]}, "events": events})
            session["revision"] += 1
        if parts[-1] == "end" or len(session["lines"]) >= 4:
            session["is_completed"] = True
            code, urgent = outcome(session["lines"])
            session["gathered_context"] = {
                "call_disposition": code, "mapped_call_disposition": code, "urgent": urgent,
            }
            name = session["initial_context"].get("caller_name")
            if name:
                session["gathered_context"]["customer_name"] = name
        return self.reply(200, session)

    def reply(self, status, body):
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


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
            cases = {case["case"]: case for case in json.loads(output)["cases"]}
            self.assertEqual(
                set(cases),
                {
                    "known_caller_greeting", "known_caller_no_detail", "known_caller_quote_status",
                    "unknown_caller_quote_status",
                    "outcome_quote_request", "outcome_urgent_leak", "outcome_complaint",
                    "outcome_sales_call", "outcome_wrong_number",
                },
            )
            outcomes = {
                case: (tuple(c["expected"]["dispositions"]), c["expected"]["urgent"])
                for case, c in cases.items() if c["expected"]["dispositions"]
            }
            self.assertEqual(
                outcomes,
                {
                    "outcome_quote_request": (("quote_request",), False),
                    "outcome_urgent_leak": (("booking_request", "message"), True),
                    "outcome_complaint": (("message",), False),
                    "outcome_sales_call": (("spam",), False),
                    "outcome_wrong_number": (("wrong_number",), False),
                },
            )
            self.assertTrue({code for codes, _ in outcomes.values() for code in codes} <= set(scenarios_caller.CODES))
            # Pre-call carries the known caller's name only (amendment A1, section 1).
            for case in ("known_caller_greeting", "known_caller_no_detail", "known_caller_quote_status"):
                self.assertEqual(set(cases[case]["initial_context"]), {"business_name", "caller_name"})
            self.assertIs(cases["known_caller_no_detail"]["expected"]["tool_called"], False)
            self.assertEqual(set(cases["unknown_caller_quote_status"]["initial_context"]), {"business_name"})
            # No record name, nobody to confirm: the lookup must not run.
            self.assertIs(cases["unknown_caller_quote_status"]["expected"]["tool_called"], False)
        self.assertEqual(self.fake.calls, [])

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

    def test_run_scores_objective_checks_and_archives_the_test_workflow(self):
        self.cli("run", "--lang", "ko", "--live-workflow-id", "1", "--repeats", "1", "--out", str(self.out))
        report = json.loads((self.out / "report.json").read_text())
        self.assertEqual(report["runs"], 9)
        self.assertEqual(report["errors"], 0)
        self.assertEqual(report["objective_passed"], 9)
        cases = report["cases"]
        for case in ("known_caller_greeting", "known_caller_no_detail", "known_caller_quote_status"):
            for check in ("name_in_first_reply", "customer_name_confirmed"):
                self.assertEqual(cases[case]["checks"][check], 1, (case, check))
        self.assertEqual(cases["known_caller_no_detail"]["checks"]["tool_not_called"], 1)
        self.assertEqual(cases["unknown_caller_quote_status"]["checks"]["tool_not_called"], 1)
        self.assertEqual(cases["known_caller_quote_status"]["checks"]["tool_called"], 1)
        self.assertEqual(cases["known_caller_quote_status"]["checks"]["tool_after_confirmation"], 1)
        self.assertEqual(cases["outcome_urgent_leak"]["dispositions"], {"booking_request": 1})
        self.assertEqual(cases["outcome_complaint"]["dispositions"], {"message": 1})
        for case in scenarios_caller.EXPECTED_OUTCOME:
            self.assertEqual(cases[case]["checks"]["urgent"], 1, case)
        (workflow_id,) = self.fake.workflows
        workflow = self.fake.workflows[workflow_id]
        self.assertEqual(workflow["status"], "archived")
        self.assertEqual(workflow["settings"]["model_configuration_override"], self.fake.override)
        self.assertEqual(
            [d["code"] for d in workflow["settings"]["call_dispositions"]], list(scenarios_caller.CODES)
        )
        sessions = [c for c in self.fake.calls if c[0] == "POST" and c[1].endswith("/text-chat/sessions")]
        self.assertEqual(sessions[0][2]["initial_context"]["caller_name"], "홍길동")
        # The live workflow is read for its model only; nothing writes to it.
        self.assertFalse(any(c[0] != "GET" and "/workflow/1" in c[1] for c in self.fake.calls))
        row = json.loads((self.out / "ko_known_caller_quote_status-r1.json").read_text())
        self.assertEqual(row["grade"]["tool_results"], [{"status": "success", "data": {"known": False}}])
        self.assertEqual(json.loads((self.out / "state.json").read_text()), {"workflows": []})

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
        # The lookup may not run before the caller confirmed the name.
        case = next(c for c in scenarios_caller.build_cases("en") if c["case"] == "known_caller_quote_status")
        tool = {"type": "tool_call_started", "payload": {"function_name": "lookup_caller_work_en"}}
        graded = scenarios_caller.grade(
            case, [case["expected"]["generic_opening"]],
            [turn("Am I speaking with Alex Morgan?", tool), turn("Thanks.")],
            {"call_disposition": "message", "customer_name": "Alex Morgan"}, "lookup_caller_work_en",
        )
        self.assertTrue(graded["checks"]["tool_called"])
        self.assertFalse(graded["checks"]["tool_after_confirmation"])
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

    def test_refusals_happen_before_any_write(self):
        self.cli("run", "--lang", "ko", "--live-workflow-id", "3", "--out", str(self.out), expected=1)
        self.fake.tools = [t for t in self.fake.tools if t["name"] != "lookup_caller_work_ko"]
        output = self.cli("run", "--lang", "ko", "--live-workflow-id", "1", "--out", str(self.out), expected=1)
        self.assertIn("Run golive --apply first", output)
        self.assertFalse(any(c[0] != "GET" for c in self.fake.calls))

    def test_cleanup_archives_what_a_crashed_run_left(self):
        self.out.mkdir(parents=True)
        self.fake.workflows[777] = {"status": "active"}
        (self.out / "state.json").write_text(json.dumps({"workflows": [777]}))
        output = self.cli("cleanup", "--out", str(self.out))
        self.assertIn("archived test workflow 777", output)
        self.assertEqual(self.fake.workflows[777]["status"], "archived")


if __name__ == "__main__":
    unittest.main()
