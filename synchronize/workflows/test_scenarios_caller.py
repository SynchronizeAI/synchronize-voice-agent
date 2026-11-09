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
        # The English line's own End Call tools, as go-live creates them.
        self.tools += [
            {**copy.deepcopy(spec["tool"]), "tool_uuid": str(uuid4()), "status": "active"}
            for spec in golive.end_call_specs("HEAD", "en")
        ]
        # The office-visit tools as go-live creates them, wired to this fake's own origin, whose routes answer a request
        # with no secret 401 while served and 404 before Synchronize serves them.
        self.tools += [office_tool(spec, self.origin) for spec in golive.office_visit_specs("HEAD", "en")]
        self.office_routes = {golive.AVAILABILITY_ROUTE, golive.BOOK_ROUTE}
        # What the office-visit routes answer a text chat: unknown (it is not a phone call under way).
        self.office_answer = {"status": "success", "status_code": 200, "data": dict(golive.OFFICE_UNKNOWN)}


def office_tool(spec, origin):
    """A live office-visit tool as Dograh lists it: the committed template, wired to origin."""
    tool = copy.deepcopy(spec["tool"])
    tool["definition"]["config"].update(url=origin + spec["route"], credential_uuid=str(uuid4()))
    return {**tool, "tool_uuid": str(uuid4()), "status": "active"}


# The times the office-visit scripts name, as the fake agent sends them (2026-11-16 is a Monday).
OFFICE_TIMES = {"Monday at 10 AM": "2026-11-16T10:00:00", "Monday at 7 in the morning": "2026-11-16T07:00:00"}


def live_tool(lang, tool_uuid):
    """The live lookup tool as Dograh lists it: the committed template, wired."""
    tool = copy.deepcopy(golive.tool_spec("HEAD", lang)["tool"])
    tool["definition"]["config"].update(url="https://runtime.example/api/voice/tools/caller-work", credential_uuid=str(uuid4()))
    return {**tool, "tool_uuid": tool_uuid, "status": "active"}


# Words of a question about a visit; the fake looks up only a caller who confirmed the name.
VISIT_WORDS = ("방문", "오시는지", "visit", "coming out")
# Words of an urgent matter: a leak, or the business's own urgent example (no heat in winter).
URGENT_WORDS = ("ceiling", "heat has been out")
# The English line's caller who does not want to be recorded, a caller in Spanish and one in Portuguese: each call ends
# at once through the line's own End Call tool for it.
OBJECTION, SPANISH, PORTUGUESE = "don't want this call recorded", "Hola", "Olá"
LANGUAGE = {"end_call_spanish": "es", "end_call_other_language": "pt"}
# The booking scripts' email as the extraction is told to write it: the address the caller confirmed, with their correction.
EMAILS = (("l, e, a after the dot", "jamie.lea@gmail.com"), (scenarios_caller.BOOKING_EMAIL_SAID, scenarios_caller.BOOKING_EMAIL),
          *scenarios_caller.VISIT_EMAILS.values())


def outcome(lines):
    """The fake classifier: a code, and the separate urgent flag."""
    text = " ".join(lines)
    for words, code, urgent in (
        (("천장", "가스", "gas smell", *URGENT_WORDS), "booking_request", True),
        (("대출", "card processing"), "spam", False),
        (("중국집", "pizza"), "wrong_number", False),
        (("move the estimate visit", "cancel the estimate visit", "come out to measure", "come by your office",
          "Could someone come"), "booking_request", False),
        (("견적 좀", "a quote for", "a quote to", "new deck", "presupuesto"), "quote_request", False),
    ):
        if any(word in text for word in words):
            return code, urgent
    return "message", False


def ending_tool(lines):
    """The End Call tool the fake agent calls on the caller's latest line, in a turn of its own, or None."""
    if OBJECTION in lines[-1]:
        return "end_call_recording_declined"
    if len(lines) == 1 and lines[0].startswith(SPANISH):
        return "end_call_spanish"
    if len(lines) == 1 and lines[0].startswith(PORTUGUESE):
        return "end_call_other_language"
    return None


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
        if self.path.startswith("/api/voice/tools/"):
            # The runtime's office-visit routes, asked with no secret (scenarios_caller.office_tools_live).
            return self.reply(401 if self.path in fake.office_routes else 404, {})
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
            greeting = scenarios_caller.opening("ko" if "업체" in context["business_name"] else "en", context)
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
                    "events": [{"type": "bot_speech", "payload": {"text": greeting}}],
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
            ending = ending_tool(session["lines"])
            if ending:
                # A tool-only turn: the call and the tool's fixed message, nothing the model says.
                (tool,) = [t for t in fake.tools if t["name"] == ending and t["status"] == "active"]
                session["ended_by"] = ending
                events.append({"type": "tool_call_started", "payload": {"function_name": ending, "arguments": {}}})
                reply = tool["definition"]["messages"][0]["text"]
            elif len(session["lines"]) == 1 and name:
                reply = f"네, 혹시 {name}님 맞으세요?"
            elif len(session["lines"]) == 2 and confirmed(session) and session["initial_context"].get("recent_jobs"):
                # A returning caller who confirmed the name is asked about the newest job on the record.
                job = session["initial_context"]["recent_jobs"].splitlines()[0].removeprefix("- ").split(":")[0]
                reply = f"Is this about the {job.lower()} we did last time?"
            elif next((at for said, at in OFFICE_TIMES.items() if said in body["text"]), None) and wired(
                fake, workflow_id, "check_office_availability"
            ):
                # The office-visit check for the time the caller named, answered as the fake's routes answer.
                tool = wired(fake, workflow_id, "check_office_availability")
                at = next(at for said, at in OFFICE_TIMES.items() if said in body["text"])
                events.append({"type": "tool_call_started", "payload": {"function_name": tool["name"], "arguments": {"starts_at": at}}})
                events.append({"type": "bot_speech", "payload": {"text": tool["definition"]["messages"][0]["text"]}})
                events.append({"type": "tool_call_result", "payload": {"function_name": tool["name"], "result": fake.office_answer}})
                reply = "I'm sorry, I can't confirm that time right now; the office will confirm it with you."
            elif len(session["lines"]) == 2 and confirmed(session) and any(
                word in session["lines"][0] for word in VISIT_WORDS
            ):
                # Only a caller who confirmed the record's name is looked up.
                (uuid,) = [u for n in fake.workflows[workflow_id]["definition"]["nodes"] for u in n["data"].get("tool_uuids") or []
                           if any(t["tool_uuid"] == u and t["name"].startswith("lookup_caller_work") for t in fake.tools)]
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
        # The fake agent ends no call itself but through the line's End Call tools: the runner ends it after the script.
        if parts[-1] == "end" or session.get("ended_by"):
            session["is_completed"] = True
            code, urgent = outcome(session["lines"])
            session["gathered_context"] = {
                "call_disposition": code, "mapped_call_disposition": code, "urgent": urgent,
            }
            # As the extraction is told: marked only when the tool ran.
            if session.get("ended_by") == "end_call_recording_declined":
                session["gathered_context"]["recording_declined"] = True
            if session.get("ended_by") in LANGUAGE:
                session["gathered_context"]["caller_language"] = LANGUAGE[session["ended_by"]]
            said = " ".join(session["lines"])
            email = next((address for words, address in EMAILS if words in said), None)
            if email:
                session["gathered_context"]["customer_email"] = email
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


def wired(fake, workflow_id, name):
    """The live tool of that name when the test workflow carries it, else None."""
    refs = {u for n in fake.workflows[workflow_id]["definition"]["nodes"] for u in n["data"].get("tool_uuids") or []}
    return next((t for t in fake.tools if t["name"] == name and t["tool_uuid"] in refs), None)


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
                # A visit's email: corrected by one letter, and declined.
                "visit_email_corrected", "visit_email_declined",
                "recording_objection", "ai_robot_question", "spanish_caller", "other_language_caller",
                # Near misses of the recording objection: none of them ends the call or marks it.
                "recording_question", "recording_one_detail", "recording_person_request", "recording_objection_withdrawn",
                # Near miss of the language rule: a Spanish hello, then the matter in English.
                "spanish_greeting_english_matter",
                # The owner's two stories: an office visit checked and booked (open, or the time not open), a returning
                # caller's site visit with the recent job raised, and a known caller with no jobs on the record.
                "office_visit_available", "office_visit_slot_taken", "returning_caller_site_visit",
                "known_caller_no_record_site_visit",
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
                        "visit_email_corrected": (("booking_request",), False),
                        "visit_email_declined": (("booking_request",), False),
                        # Ended by the agent with a call back owed, never done.
                        "recording_objection": (("quote_request", "message"), False),
                        "spanish_caller": (("quote_request", "message"), False),
                        "other_language_caller": (("quote_request", "message"), False),
                        "recording_question": (("message",), False),
                        "recording_one_detail": (("message",), False),
                        "recording_person_request": (("message",), False),
                        "recording_objection_withdrawn": (("quote_request", "booking_request"), False),
                        "spanish_greeting_english_matter": (("quote_request", "booking_request"), False),
                        # A visit asked for, booked or not: the owner sees a booking request, never urgent here.
                        "office_visit_available": (("booking_request",), False),
                        "office_visit_slot_taken": (("booking_request",), False),
                        # A roof "leaking again": the situation decides whether it is urgent, so the flag is not checked.
                        "returning_caller_site_visit": (("booking_request",), None),
                        "known_caller_no_record_site_visit": (("booking_request",), False),
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
            if lang == "en":
                known |= {"returning_caller_site_visit", "known_caller_no_record_site_visit"}
            fact_keys = {"service_area", "pricing_policy", "open_now", "hours_today", "next_open"}
            for case, c in cases.items():
                # Pre-call carries the known caller's name only (amendment A1, section 1); business facts and the
                # urgent examples come only in their own cases. Every English call carries the owner's name the company saved.
                expected_keys = set(scenarios_caller.BUSINESS[lang]) | ({"caller_name"} if case in known else set())
                extra = set(c["initial_context"]) - expected_keys
                if case in facts:
                    self.assertTrue(extra <= fact_keys and {"service_area", "pricing_policy"} <= extra, case)
                elif case == "returning_caller_site_visit":
                    # Pre-call's recent jobs for a returning caller (Synchronize PR #3932), under that caller's own name.
                    self.assertEqual(extra, {"recent_jobs"}, case)
                    self.assertEqual(c["initial_context"]["caller_name"], "Michael Brown")
                    self.assertTrue(c["initial_context"]["recent_jobs"].startswith("- Roof shingle repair: done;"))
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
                "INTAKE_MISSED": {
                    "kind_new_project", "kind_ongoing_project", "kind_problem_warranty", "visit_move", "visit_cancel",
                    "visit_email_corrected", "visit_email_declined",
                },
                # The visits ask for an email, an urgent "please come" or "please send someone" too (urgency adds); a new
                # project with no visit asked for and a question about the next visit (known, unknown, or a lookup that
                # fails) do not.
                "EMAIL_MISHANDLED": {
                    "visit_move", "visit_cancel", "visit_email_corrected", "visit_email_declined", "kind_new_project",
                    "outcome_urgent_leak", "outcome_urgent_gas", "known_caller_visit", "unknown_caller_visit", "lookup_failure",
                    "office_visit_available", "office_visit_slot_taken", "returning_caller_site_visit",
                    "known_caller_no_record_site_visit",
                },
                "RECORDING_MISHANDLED": {
                    "recording_objection", "recording_question", "recording_one_detail", "recording_person_request",
                    "recording_objection_withdrawn",
                },
                "LANGUAGE_NOT_HANDLED": {"spanish_caller", "other_language_caller", "spanish_greeting_english_matter"},
            }
            for flag, gated in line_flags.items():
                self.assertEqual(flag in manifest["flags"], lang == "en")
                self.assertEqual({case for case, c in cases.items() if flag in c["gate"]}, gated if lang == "en" else set())
            if lang == "en":
                # Both values Synchronize reads after an English call are checked on every English case: a recording_declined on
                # an ordinary call would erase its details. The objection and the other languages end through their own tool,
                # on the line named, and no other case may run any of those tools.
                default = {"recording_declined": False, "caller_language": "", "customer_email": ""}
                messages = {spec["tool"]["name"]: spec["tool"]["definition"]["messages"][0]["text"]
                            for spec in golive.end_call_specs("HEAD", "en")}
                expected = {
                    "recording_objection": ({**default, "recording_declined": True}, ("end_call_recording_declined", 1)),
                    "spanish_caller": ({**default, "caller_language": "es"}, ("end_call_spanish", 0)),
                    "other_language_caller": ({**default, "caller_language": "pt"}, ("end_call_other_language", 0)),
                    # Only a visit whose caller gave an email carries one: the address confirmed, after the correction.
                    "visit_move": ({**default, "customer_email": "jamie.lee@gmail.com"}, None),
                    "visit_cancel": ({**default, "customer_email": "jamie.lee@gmail.com"}, None),
                    "visit_email_corrected": ({**default, "customer_email": "jamie.lea@gmail.com"}, None),
                    # An urgent leak with "please come": the visit's email is taken too (urgency adds).
                    "outcome_urgent_leak": ({**default, "customer_email": "jamie.lee@gmail.com"}, None),
                    "office_visit_available": ({**default, "customer_email": "sarah.miller@gmail.com"}, None),
                    "office_visit_slot_taken": ({**default, "customer_email": "sarah.miller@gmail.com"}, None),
                    "returning_caller_site_visit": ({**default, "customer_email": "michael.brown@gmail.com"}, None),
                    "known_caller_no_record_site_visit": ({**default, "customer_email": "alex.morgan@gmail.com"}, None),
                }
                for case, c in cases.items():
                    contract, ends = expected.get(case, (default, None))
                    self.assertEqual(c["expected"]["contract"], contract, case)
                    self.assertEqual(c["expected"]["end_tools"], sorted(messages), case)
                    self.assertEqual(
                        c["expected"]["ends"],
                        {"tool": ends[0], "line": ends[1], "message": messages[ends[0]]} if ends else None, case,
                    )
                    if ends:
                        # The ending line is the script's last: nothing waits for another caller turn.
                        self.assertEqual(len(c["lines"]), ends[1] + 1, case)
                for case in ("recording_question", "recording_one_detail", "recording_person_request", "recording_objection_withdrawn"):
                    self.assertNotIn("don't want this call recorded", " ".join(cases[case]["lines"]), case)
                # Asking about the recording, also whether it can be switched off, is not an objection.
                self.assertIn("Can you switch the recording off?", cases["recording_question"]["lines"])
                # The language near miss opens with a Spanish hello alone and tells the matter in English.
                self.assertEqual(cases["spanish_greeting_english_matter"]["lines"][:2], ["¿Bueno?", "Hi, sorry, I need a quote for a new fence."])
                # A non-project reason keeps the read-back gate away from the new-project intake.
                self.assertIn("invoice", cases["callback_other_number"]["lines"][0])
                self.assertTrue(cases["spanish_caller"]["lines"][0].startswith("Hola"))
                self.assertIn("robot", cases["ai_robot_question"]["lines"][1])
                self.assertEqual(cases["visit_cancel"]["lines"][2], "No thanks, I'd rather just cancel.")
                # Each visit script answers the email question: with the address (then yes to the read-back), with a
                # one-letter correction, or with a no; no other script says an email.
                said = scenarios_caller.BOOKING_EMAIL_SAID
                for case in ("visit_move", "visit_cancel"):
                    lines = cases[case]["lines"]
                    self.assertEqual(lines[lines.index(said):], [said, "Yes, that's right.", "No, that's all."], case)
                self.assertEqual(cases["visit_email_corrected"]["lines"][2:4], [said, "Almost: it's l, e, a after the dot, not l, e, e."])
                self.assertIn("rather not give an email", cases["visit_email_declined"]["lines"][4])
                # An urgent caller who asks for someone to come is asked for an email too (urgency adds): the leak caller
                # gives one after the callback number, the gas caller declines.
                self.assertEqual(cases["outcome_urgent_leak"]["lines"][3:6],
                                 [scenarios_caller.URGENT_NUMBER_ANSWER, said, "Yes, that's right."])
                self.assertIn("rather not give an email", cases["outcome_urgent_gas"]["lines"][4])
                for case, c in cases.items():
                    if case not in ("visit_move", "visit_cancel", "visit_email_corrected", "visit_email_declined", "outcome_urgent_gas"):
                        self.assertNotRegex(" ".join(c["lines"]).lower(), r"\be-?mail\b", case)
            else:
                for c in cases.values():
                    self.assertFalse({"contract", "ends", "end_tools"} & set(c["expected"]))
                    self.assertFalse(any(
                        key in line for key in ("recording_declined", "caller_language", "customer_email") for line in c["criteria"]
                    ))
            if lang == "en":
                for case in marked:
                    # The answer sits between the detail question and the recap's "anything to add"; a caller who asked
                    # for someone to come then answers the visit's email question (an address and its read-back, or a no).
                    self.assertEqual(cases[case]["lines"].index("This number is fine."), 3, case)
                    self.assertEqual(len(cases[case]["lines"]), {"outcome_urgent_leak": 7, "outcome_urgent_gas": 6}.get(case, 5), case)
            self.assertEqual({c["workflow"] for case, c in cases.items() if case != "lookup_failure"}, {"live"})
            self.assertEqual(cases["facts_after_hours"]["initial_context"]["open_now"], "no")
            self.assertTrue(cases["facts_after_hours"]["initial_context"]["next_open"])
            self.assertEqual(set(cases["facts_not_set"]["initial_context"].values()) - set(scenarios_caller.BUSINESS[lang].values()), {""})
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
                    # A new caller who wants to come to the office has no record to look up.
                    **({"office_visit_available": False, "office_visit_slot_taken": False} if lang == "en" else {}),
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

    def test_english_test_workflow_wires_the_lookup_and_the_live_end_call_tools(self):
        lookup = str(uuid4())
        ends = {spec["tool"]["name"]: str(uuid4()) for spec in golive.end_call_specs("HEAD", "en")}
        # Without the live office-visit tools (golive creates them once the runtime serves their routes) they are left off.
        _, deployed = scenarios_caller.test_definition("en", lookup, ends)
        refs = golive.nodes(deployed)["1"]["data"]["tool_uuids"]
        self.assertEqual(refs[-4:], [lookup, *(ends[spec["tool"]["name"]] for spec in golive.end_call_specs("HEAD", "en"))])
        self.assertFalse(any(ref.startswith(golive.PLACEHOLDER_UUID_PREFIX) for ref in refs))
        office = {name: str(uuid4()) for name in scenarios_caller.OFFICE_TOOLS}
        _, deployed = scenarios_caller.test_definition("en", lookup, ends, office)
        refs = golive.nodes(deployed)["1"]["data"]["tool_uuids"]
        self.assertEqual(refs[-2:], [office["check_office_availability"], office["book_office_visit"]])
        self.assertFalse(any(ref.startswith(golive.PLACEHOLDER_UUID_PREFIX) for ref in refs))
        # Without the live End Call tools there is no English test workflow (go-live creates them).
        with self.assertRaises(golive.GoLiveError):
            scenarios_caller.test_definition("en", lookup)

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
            self.assertFalse({"recording_declined", "caller_language", "customer_email", "end_tool", "end_turn"} & set(group["checks"]), case)
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
        # The objection and the other languages end through their own tool in a tool-only turn; each carries its value for
        # Synchronize, and every English case checks both values and that no other of those tools ran.
        for case in ("recording_objection", "spanish_caller", "other_language_caller"):
            self.assertEqual(cases[case]["checks"]["end_turn"], 1, case)
        self.assertEqual(cases["recording_objection"]["checks"]["recording_declined"], 1)
        self.assertEqual(cases["spanish_caller"]["checks"]["caller_language"], 1)
        self.assertEqual(cases["other_language_caller"]["checks"]["caller_language"], 1)
        for case, group in cases.items():
            self.assertTrue({"recording_declined", "caller_language", "customer_email", "end_tool"} <= set(group["checks"]), case)
        # The visits' email, as the caller confirmed it (after the correction), and none where the caller declined.
        for case in ("visit_move", "visit_cancel", "visit_email_corrected", "visit_email_declined"):
            self.assertEqual(cases[case]["checks"]["customer_email"], 1, case)
        row = json.loads((self.out / "en_visit_email_corrected-r1.json").read_text())
        self.assertEqual(row["grade"]["review"]["extraction"]["customer_email"], "jamie.lea@gmail.com")
        for case in ("recording_question", "recording_one_detail", "recording_person_request", "recording_objection_withdrawn"):
            self.assertEqual((cases[case]["checks"]["end_tool"], cases[case]["checks"]["recording_declined"]), (1, 1), case)
        near = cases["spanish_greeting_english_matter"]["checks"]
        self.assertEqual((near["end_tool"], near["caller_language"]), (1, 1))
        row = json.loads((self.out / "en_recording_objection-r1.json").read_text())
        self.assertTrue(row["completed_naturally"])
        self.assertIs(row["grade"]["review"]["extraction"]["recording_declined"], True)
        self.assertEqual(len([t for t in row["turns"] if t.get("user_message")]), 2)
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
        # English: a recording_declined on an ordinary call fails (Synchronize would erase its details), and so does a
        # non-English caller_language on an English call.
        case = next(c for c in scenarios_caller.build_cases("en") if c["case"] == "kind_ongoing_project")
        en_opening = [case["expected"]["generic_opening"]]
        for gathered, passed in (
            ({"call_disposition": "message"}, True),
            ({"call_disposition": "message", "recording_declined": False, "caller_language": ""}, True),
            ({"call_disposition": "message", "recording_declined": True}, False),
            ({"call_disposition": "message", "recording_declined": "true"}, False),
            # A language set on an English call: no language tool ran, so nothing may be marked.
            ({"call_disposition": "message", "caller_language": "es"}, False),
        ):
            graded = scenarios_caller.grade(case, en_opening, [], gathered, "lookup_caller_work_en", True)
            self.assertEqual(graded["passed"], passed, gathered)
        # An email on a call whose caller gave none is an invented value.
        graded = scenarios_caller.grade(case, en_opening, [], {"call_disposition": "message", "customer_email": "jamie.lee@gmail.com"},
                                        "lookup_caller_work_en", True)
        self.assertFalse(graded["checks"]["customer_email"])
        # A visit's email is the address the caller confirmed: case and stray spaces aside, the corrected one only.
        case = next(c for c in scenarios_caller.build_cases("en") if c["case"] == "visit_email_corrected")
        for email, passed in (("jamie.lea@gmail.com", True), (" Jamie.Lea@Gmail.com ", True), ("jamie.lee@gmail.com", False),
                              ("jamie dot lea at gmail dot com", False), ("", False), (None, False)):
            graded = scenarios_caller.grade(case, en_opening, [], {"call_disposition": "booking_request", "customer_email": email},
                                            "lookup_caller_work_en", True)
            self.assertEqual(graded["checks"]["customer_email"], passed, email)
        case = next(c for c in scenarios_caller.build_cases("en") if c["case"] == "visit_email_declined")
        graded = scenarios_caller.grade(case, en_opening, [], {"call_disposition": "booking_request", "customer_email": ""},
                                        "lookup_caller_work_en", True)
        self.assertTrue(graded["passed"], graded["checks"])
        case = next(c for c in scenarios_caller.build_cases("en") if c["case"] == "kind_ongoing_project")
        # en and en-* are English: nothing to store.
        for language in ("en", "en-US"):
            graded = scenarios_caller.grade(case, en_opening, [], {"call_disposition": "message", "caller_language": language},
                                            "lookup_caller_work_en", True)
            self.assertTrue(graded["checks"]["caller_language"], language)
        messages = {spec["tool"]["name"]: spec["tool"]["definition"]["messages"][0]["text"]
                    for spec in golive.end_call_specs("HEAD", "en")}

        def ended(tool, *spoken):
            started = {"type": "tool_call_started", "payload": {"function_name": tool}}
            return {"user_message": {"text": "x"},
                    "events": [started, *({"type": "bot_speech", "payload": {"text": t}} for t in spoken)]}

        # The Spanish call ends in its first turn through the Spanish tool, with nothing but its message; es or es-US.
        case = next(c for c in scenarios_caller.build_cases("en") if c["case"] == "spanish_caller")
        spanish = messages["end_call_spanish"]
        for turns, gathered, completed, passed in (
            ([ended("end_call_spanish", spanish)], {"call_disposition": "message", "caller_language": "es"}, True, True),
            ([ended("end_call_spanish", spanish)], {"call_disposition": "quote_request", "caller_language": "es-US"}, True, True),
            # The model spoke itself in that turn: not a tool-only turn.
            ([ended("end_call_spanish", "Un momento.", spanish)], {"call_disposition": "message", "caller_language": "es"}, True, False),
            # Waited for another caller turn first.
            ([turn("El dueño le devolverá la llamada."), ended("end_call_spanish", spanish)],
             {"call_disposition": "message", "caller_language": "es"}, True, False),
            # Ended by the runner, or by another tool, or without its value.
            ([ended("end_call_spanish", spanish)], {"call_disposition": "message", "caller_language": "es"}, False, False),
            ([ended("end_call_other_language", messages["end_call_other_language"])],
             {"call_disposition": "message", "caller_language": "es"}, True, False),
            ([ended("end_call_spanish", spanish)], {"call_disposition": "message"}, True, False),
            ([ended("end_call_spanish", spanish)], {"call_disposition": "done", "caller_language": "es"}, True, False),
        ):
            graded = scenarios_caller.grade(case, en_opening, turns, gathered, "lookup_caller_work_en", completed)
            self.assertEqual(graded["passed"], passed, (turns, gathered, completed))
        case = next(c for c in scenarios_caller.build_cases("en") if c["case"] == "recording_objection")
        objection = [turn("May I have your name?"), ended("end_call_recording_declined", messages["end_call_recording_declined"])]
        graded = scenarios_caller.grade(case, en_opening, objection, {"call_disposition": "quote_request", "recording_declined": True},
                                        "lookup_caller_work_en", True)
        self.assertTrue(graded["passed"], graded["checks"])
        graded = scenarios_caller.grade(case, en_opening, objection, {"call_disposition": "quote_request"}, "lookup_caller_work_en", True)
        self.assertFalse(graded["checks"]["recording_declined"])
        # A near miss that ends through the recording tool fails, whatever the extraction says.
        case = next(c for c in scenarios_caller.build_cases("en") if c["case"] == "recording_question")
        graded = scenarios_caller.grade(case, en_opening, [ended("end_call_recording_declined", messages["end_call_recording_declined"])],
                                        {"call_disposition": "message"}, "lookup_caller_work_en", True)
        self.assertFalse(graded["checks"]["end_tool"])
        graded = scenarios_caller.grade(case, en_opening, [turn("Yes, this call is recorded. How may I help you?")],
                                        {"call_disposition": "message"}, "lookup_caller_work_en", True)
        self.assertTrue(graded["passed"], graded["checks"])

    def test_office_visit_cases_are_skipped_with_the_reason_without_their_tools_or_routes(self):
        office = ("office_visit_available", "office_visit_slot_taken")
        everything = len(scenarios_caller.build_cases("en"))
        for absent, reason in (
            ("routes", "the runtime answers 404 for /api/voice/tools/availability: Synchronize PR #3935 (or #3926) is not deployed"),
            ("tools", "no live check_office_availability tool: golive creates it once the runtime serves"),
        ):
            with self.subTest(absent=absent):
                if absent == "routes":
                    self.fake.office_routes = set()
                else:
                    self.fake.office_routes = {golive.AVAILABILITY_ROUTE, golive.BOOK_ROUTE}
                    self.fake.tools = [t for t in self.fake.tools if t["name"] not in scenarios_caller.OFFICE_TOOLS]
                out = self.out / absent
                output = self.cli("run", "--lang", "en", "--live-workflow-id", "3", "--repeats", "1", "--out", str(out))
                self.assertIn(f"office-visit cases skipped ({', '.join(office)}): {reason}", output)
                report = json.loads((out / "report.json").read_text())
                self.assertEqual(report["runs"], everything - 2)
                self.assertEqual(report["errors"], 0)
                self.assertEqual(set(report["skipped"]), set(office))
                self.assertTrue(all(why.startswith(reason) for why in report["skipped"].values()))
                self.assertFalse(set(office) & set(report["cases"]))
                self.assertIn('"skipped": 2', output)
                # The rest still runs, the site visits among them; the test workflows carry no office-visit tool.
                self.assertEqual(report["objective_passed"], report["runs"], json.dumps(report["cases"])[:2000])
                self.assertIn("returning_caller_site_visit", report["cases"])
                for workflow in list(self.fake.workflows.values())[-2:]:
                    refs = {u for n in workflow["definition"]["nodes"] for u in n["data"].get("tool_uuids") or []}
                    self.assertFalse(refs & {t["tool_uuid"] for t in self.fake.tools if t["name"] in scenarios_caller.OFFICE_TOOLS})
                    self.assertFalse(any(ref.startswith(golive.PLACEHOLDER_UUID_PREFIX) for ref in refs))
        # The route check carries no secret and no API key, and nothing but the Dograh API and those routes is asked.
        self.assertTrue(all(c[1].startswith(("/api/v1/", "/api/voice/tools/")) for c in self.fake.calls))

    def test_office_and_recent_job_checks_follow_what_the_tools_answered(self):
        cases = {c["case"]: c for c in scenarios_caller.build_cases("en")}
        opening = [cases["office_visit_available"]["expected"]["generic_opening"]]
        gathered = {"call_disposition": "booking_request", "customer_email": "sarah.miller@gmail.com"}

        def turn(text, *events):
            return {"user_message": {"text": "x"}, "events": [*events, {"type": "bot_speech", "payload": {"text": text}}]}

        def ran(name, arguments, data):
            result = {"status": "success", "status_code": 200, "data": data} if data else {"status": "error", "error": "timed out"}
            return (
                {"type": "tool_call_started", "payload": {"function_name": name, "arguments": arguments}},
                {"type": "tool_call_result", "payload": {"function_name": name, "result": result}},
            )

        monday = {"starts_at": "2026-11-16T10:00:00"}
        open_ = {"status": "available", "starts_at": "2026-11-16T10:00:00-08:00", "spoken": "Monday", "alternatives": []}
        booked = {**open_, "status": "confirmed"}
        unknown = dict(golive.OFFICE_UNKNOWN)

        def grade(case, *turns):
            return scenarios_caller.grade(cases[case], opening, list(turns), gathered, "lookup_caller_work_en", True)

        # A real call: open, then booked, and the agent says so.
        graded = grade("office_visit_available", turn("May I have your name?"), turn("What day and time?"),
                       turn("Monday at ten is open. What's the best email?", *ran("check_office_availability", monday, open_)),
                       turn("Your visit is booked for Monday at ten.", *ran("book_office_visit", monday, booked)))
        self.assertTrue(graded["passed"], graded["checks"])
        self.assertTrue(graded["checks"]["booking_said"])
        self.assertEqual([r["answer"]["status"] for r in graded["office_results"]], ["available", "confirmed"])
        # A text chat: unknown, a request taken, never booked.
        graded = grade("office_visit_available", turn("May I have your name?"),
                       turn("I'm sorry, I can't confirm that right now; the office will confirm it with you.",
                            *ran("check_office_availability", monday, unknown)))
        self.assertTrue(graded["passed"], graded["checks"])
        # Booked words with no confirmed booking, a booking of a time no check offered, a check after unknown, a wrong time,
        # no check at all: each fails its own check.
        for turns, check in (
            ([turn("You're booked for Monday at ten.", *ran("check_office_availability", monday, unknown))], "never_said_booked"),
            ([turn("You're all set for Monday.", *ran("check_office_availability", monday, open_))], "never_said_booked"),
            ([turn("One moment.", *ran("check_office_availability", monday, open_),
                   *ran("book_office_visit", {"starts_at": "2026-11-16T11:00:00"}, booked))], "booked_only_offered"),
            ([turn("Let me check.", *ran("check_office_availability", monday, unknown), *ran("check_office_availability", monday, unknown))],
             "no_call_after_unknown"),
            ([turn("Let me check.", *ran("check_office_availability", monday, None), *ran("check_office_availability", monday, open_))],
             "no_call_after_unknown"),
            ([turn("Let me check.", *ran("check_office_availability", {"starts_at": "2026-11-17T10:00:00"}, open_))],
             "availability_time"),
            ([turn("The office will confirm Monday with you.")], "availability_called"),
            # Booked although the booking answered unknown.
            ([turn("Booked.", *ran("check_office_availability", monday, open_), *ran("book_office_visit", monday, unknown))],
             "never_said_booked"),
            # Confirmed, but the caller was never told.
            ([turn("Thanks.", *ran("check_office_availability", monday, open_), *ran("book_office_visit", monday, booked))],
             "booking_said"),
        ):
            graded = grade("office_visit_available", *turns)
            self.assertFalse(graded["checks"][check], (check, graded["checks"]))
        # A window for "next week" that answered unknown is not followed by an exact time: no exact time is fine then.
        week = {"window_start": "2026-11-16T09:00:00", "window_end": "2026-11-20T17:00:00"}
        graded = grade("office_visit_available", turn("I'm sorry, the office will confirm.", *ran("check_office_availability", week, unknown)))
        self.assertTrue(graded["checks"]["availability_time"], graded["checks"])
        graded = grade("office_visit_available", turn("Which time works?", *ran("check_office_availability", week, open_)))
        self.assertFalse(graded["checks"]["availability_time"])
        # Not open: an offered alternative is bookable, the time asked for is not; the case asks for 7 AM.
        taken = {"status": "unavailable", "starts_at": "2026-11-16T07:00:00-08:00", "spoken": "x",
                 "alternatives": [{"starts_at": "2026-11-16T09:00:00-08:00", "spoken": "y"}]}
        seven = {"starts_at": "2026-11-16T07:00:00"}
        for chosen, passed in (("2026-11-16T09:00:00", True), ("2026-11-16T09:00:00-08:00", True), ("2026-11-16T07:00:00", False)):
            graded = grade("office_visit_slot_taken", turn("Seven isn't open; nine is. Does that work?", *ran("check_office_availability", seven, taken)),
                           turn("You're booked for nine.", *ran("book_office_visit", {"starts_at": chosen}, {**booked, "starts_at": chosen})))
            self.assertEqual(graded["checks"]["booked_only_offered"], passed, chosen)
        # A visit to the caller: neither office-visit tool, nothing booked; the returning caller's job named after the confirmation.
        gathered.update(customer_name="Michael Brown", customer_email="michael.brown@gmail.com")
        michael = [turn("Am I speaking with Michael Brown?"), turn("Is this about the shingle repair we did last time?"),
                   turn("I'll pass Monday on to the office, who will confirm by email.")]
        graded = grade("returning_caller_site_visit", *michael)
        self.assertTrue(graded["passed"], graded["checks"])
        for turns, check in (
            ([michael[0], turn("Thanks."), michael[2]], "recent_job_raised"),
            # Raised before the confirmation only: not counted (the judges' RECORD_UNCONFIRMED gates that).
            ([turn("Michael Brown? Is this about the shingles?"), turn("Thanks."), michael[2]], "recent_job_raised"),
            ([*michael[:2], turn("Your visit is confirmed for Monday.")], "never_said_booked"),
            ([*michael[:2], turn("Let me check.", *ran("check_office_availability", monday, unknown))], "office_tools_not_called"),
        ):
            graded = grade("returning_caller_site_visit", *turns)
            self.assertFalse(graded["checks"][check], (check, graded["checks"]))
        # "The office will confirm" and "a confirmation email" are not booked words.
        self.assertIsNone(scenarios_caller.BOOKED.search("The office will confirm it, and you'll get a confirmation email."))

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
        for flag in set(scenarios_caller.FLAGS_EN) - set(scenarios_caller.FLAGS):
            self.assertNotIn(flag, text)
        # WRONG_LANGUAGE and RESTATES_CALLER keep their shared wording on Korean; English has its own. Korean asks for no email.
        self.assertIn(f"- WRONG_LANGUAGE: {scenarios_caller.FLAGS['WRONG_LANGUAGE']}\n", text)
        self.assertIn(f"- RESTATES_CALLER: {scenarios_caller.FLAGS['RESTATES_CALLER']}\n", text)
        self.assertNotRegex(text.lower(), r"\be-?mail\b|이메일")
        self.assertIn("[LOOKUP RESULT: {\"status\": \"error\"", text)
        self.assertIn("CALLER RECORD NAME: 홍길동", text)
        # The Korean line has no owner's name: not in its pre-call, its greeting or its judge items.
        self.assertNotIn("OWNER NAME", text)
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
        # The owner's name the company saved is on every English item, canaries included, so it marks none of them;
        # the greeting says it and the rubric tells the judge whose name it is.
        for case, item in by_case.items():
            self.assertIn("\nOWNER NAME: Jordan Hale\n", item, case)
            self.assertIn("BOT: Thanks for calling BUSINESS_NAME_TEST. Jordan Hale can't get to the phone right now", item, case)
        self.assertIn("OWNER NAME is the name the business set for its owner", text)
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
        self.assertIn("marked the matter urgent for the owner, who will call them back; it promises no time. Urgency is judged", text)
        self.assertIn("a visit the caller asks for is still requested or booked, with its email. It asks each kind of call", text)
        # The recording and language rules: an End Call tool ends the call at once, and the judge sees which.
        self.assertIn("A [CALL ENDED BY ...] line names the tool that ended a call.\nEach ITEM is one call", text)
        # English WRONG_LANGUAGE leaves out the fixed sentence in the caller's language and the fixed goodbye.
        self.assertIn(f"- WRONG_LANGUAGE: {scenarios_caller.FLAGS_EN['WRONG_LANGUAGE']}\n", text)
        self.assertIn("fixed goodbye, are not this flag", scenarios_caller.FLAGS_EN["WRONG_LANGUAGE"])
        for canary, flag in (
            ("bad_intake_missed", "INTAKE_MISSED"),
            ("bad_email_pressed", "EMAIL_MISHANDLED"),
            ("bad_recording_ignored", "RECORDING_MISHANDLED"),
            ("bad_language_not_handled", "LANGUAGE_NOT_HANDLED"),
        ):
            self.assertIn(f"- {flag}: ", text)
            self.assertEqual(planted[f"CANARY_{canary}"], [flag])
        # One Spanish sentence and the fixed goodbye are the rule: that canary is clean, so a judge reading it as
        # WRONG_LANGUAGE is not counted; so is a correct objection ending, and a Spanish hello ended in Spanish is flagged.
        self.assertEqual(planted["CANARY_good_language_handled"], [])
        self.assertEqual(planted["CANARY_good_recording_handled"], [])
        self.assertIn("[CALL ENDED BY end_call_recording_declined]\nBOT: Recording can't be switched off on this line",
                      by_case["CANARY_good_recording_handled"])
        self.assertEqual(planted["CANARY_bad_language_ended_too_soon"], ["LANGUAGE_NOT_HANDLED"])
        self.assertIn("CALLER: Hola, buenos días. Hi, I need a quote for a new fence.\n[CALL ENDED BY end_call_spanish]",
                      by_case["CANARY_bad_language_ended_too_soon"])
        self.assertIn("or the call ended that way although the caller told their matter in English", text)
        self.assertIn("[CALL ENDED BY end_call_spanish]\nBOT: El dueño le devolverá la llamada a este número.",
                      by_case["CANARY_good_language_handled"])
        # The canary's turn renders like a real Spanish session's.
        self.assertEqual(by_case["CANARY_good_language_handled"].split("CALLER: ", 1)[1].split("\nAFTER THE CALL")[0],
                         by_case["spanish_caller"].split("CALLER: ", 1)[1].split("\nAFTER THE CALL")[0])
        # An email read back with only the misheard part spelled, and the corrected letter spelled once more, is clean; a
        # read-back is not RESTATES_CALLER on English, whose wording of that shared flag exempts it.
        self.assertEqual(planted["CANARY_good_email_read_back"], [])
        self.assertIn("BOT: That's j, a, m, i, e dot l, e, e at gmail dot com. Is that right?", by_case["CANARY_good_email_read_back"])
        self.assertIn(f"- RESTATES_CALLER: {scenarios_caller.FLAGS_EN['RESTATES_CALLER']}\n", text)
        self.assertIn("or a number or an email address read back to confirm it", text)
        self.assertIn("When the caller wants to book, move or cancel a visit, urgent or not, it also asks once for an email", text)
        self.assertIn("Urgency is judged by what is happening now, not by a word such as leak, and only adds", text)
        self.assertEqual(len(scenarios_caller.CANARIES["en"]), 21)
        # A lookup shows as a lookup, an End Call tool as the end of the call, an office-visit tool as its check or booking
        # with the model's arguments and the result; nothing else a session records reaches a judge.
        self.assertEqual(
            {line.split(":")[0] for t in items.values() for line in t.splitlines() if line.startswith("[")},
            {"[RECORD LOOKUP RAN, ARGUMENTS", "[LOOKUP RESULT", "[CALL ENDED BY end_call_recording_declined]",
             "[CALL ENDED BY end_call_spanish]", "[CALL ENDED BY end_call_other_language]",
             "[OFFICE TIME CHECK RAN, ARGUMENTS", "[OFFICE TIME CHECK RESULT",
             "[OFFICE VISIT BOOKING RAN, ARGUMENTS", "[OFFICE VISIT BOOKING RESULT"},
        )
        # The office canaries render like the real office sessions: the check with its arguments, the tool's start message
        # said by the phone system, the result; a booking told only after a confirmed booking result is clean.
        self.assertEqual(planted["CANARY_good_office_visit_booked"], [])
        self.assertEqual(planted["CANARY_bad_office_booked_unconfirmed"], ["PROMISE"])
        for transcript in (by_case["CANARY_good_office_visit_booked"], by_case["office_visit_available"]):
            self.assertIn('[OFFICE TIME CHECK RAN, ARGUMENTS: {"starts_at": "2026-11-16T10:00:00"}]\n'
                          '[OFFICE TIME CHECK RESULT: {"status": "success", "status_code": 200, "data": {"status": ', transcript)
            self.assertIn("BOT: Let me check. ", transcript)
        self.assertIn('[OFFICE VISIT BOOKING RESULT: {"status": "success", "status_code": 200, "data": {"status": "confirmed"',
                      by_case["CANARY_good_office_visit_booked"])
        self.assertIn("is not this flag either; saying a visit is booked or confirmed without such a booking result is.", text)
        self.assertIn("say a visit is booked only after a booking result whose status is confirmed", text)
        # A returning caller's recent jobs reach the judge with the record name; a job raised before the confirmation is
        # RECORD_UNCONFIRMED, one raised once after it is clean, and work no line lists is RECORD_INVENTED.
        jobs = ("CALLER RECORD RECENT JOBS: Roof shingle repair: done; finished 2026-10-14; last visit 2026-10-13 (Replace damaged "
                "shingles); Gutter cleaning: done; finished 2026-06-02\n")
        for case in ("returning_caller_site_visit", "CANARY_good_recent_job_raised", "CANARY_bad_recent_job_unconfirmed"):
            self.assertIn("CALLER RECORD NAME: Michael Brown\n" + jobs, by_case[case], case)
        self.assertNotIn("RECENT JOBS:", by_case["known_caller_no_record_site_visit"])
        self.assertEqual(planted["CANARY_good_recent_job_raised"], [])
        self.assertEqual(planted["CANARY_bad_recent_job_unconfirmed"], ["RECORD_UNCONFIRMED"])
        self.assertIn("(an item with no RECENT JOBS line has none)", text)
        self.assertEqual(set(scenarios_caller.LOOKUP_TOOLS), {golive.tool_spec("HEAD", lang)["tool"]["name"] for lang in ("ko", "en")})

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
        # The English wording of a shared flag that gates no case is no gate flag: a judge that reads the clean email
        # read-back as RESTATES_CALLER still counts; one that reads it as EMAIL_MISHANDLED does not.
        email = item_of("CANARY_good_email_read_back")
        for flag, counted in (("RESTATES_CALLER", ["a", "b"]), ("EMAIL_MISHANDLED", ["a"])):
            rows = json.loads(self.answer(key).split("\n", 1)[1])
            for row in rows:
                if row["item"] == email:
                    row["flags"] = [flag]
            (self.out / "judge.b.out").write_text(json.dumps(rows))
            (self.out / "judge.a.out").write_text(self.answer(key))
            self.cli("score", "--out", str(self.out), expected=0 if len(counted) == 2 else 1)
            self.assertEqual(json.loads((self.out / "judged.json").read_text())["counted_judges"], counted, flag)


if __name__ == "__main__":
    unittest.main()
