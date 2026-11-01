"""Offline checks for the proof's input boundary and completion evidence."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import scenarios_precall as scenarios


class PrecallProofTests(unittest.TestCase):
    def test_focused_run_writes_only_144_english_polite_and_new_transcripts(self):
        calls = []

        def session(workflow_id, name, lines, filler):
            calls.append((workflow_id, name))
            return {"completed": True, "turns": []}

        proof = types.SimpleNamespace(
            api=lambda *a, **kw: None, session=session,
            scenarios=types.SimpleNamespace(EN_FILLER=[], KO_FILLER=[]))
        spec = types.SimpleNamespace(loader=types.SimpleNamespace(exec_module=lambda module: None))
        with tempfile.TemporaryDirectory() as out:
            args = types.SimpleNamespace(only="en-courtesy", repeats=4, out=Path(out),
                                         polite_en_workflow=10, new_en_workflow=20)
            with patch.dict(scenarios.os.environ, {"DOGRAH_PROOF_BASE_URL": "http://offline.invalid",
                                                  "DOGRAH_PROOF_API_KEY": "offline"}), \
                    patch.object(scenarios.importlib.util, "spec_from_file_location", return_value=spec), \
                    patch.object(scenarios.importlib.util, "module_from_spec", return_value=proof), \
                    patch("builtins.print"):
                scenarios.run(args)
            self.assertEqual(len(calls), 144)
            sources = json.loads((args.out / "sources.json").read_text())
            self.assertEqual(set(sources), {"polite", "new"})
            self.assertTrue(all(set(pair) == {"en"} for pair in sources.values()))
            self.assertEqual(sources["polite"]["en"]["source_ref"], "940fdef")
            self.assertEqual(len(list(args.out.glob("polite-en_*.json"))), 72)
            self.assertEqual(len(list(args.out.glob("new-en_*.json"))), 72)
            self.assertFalse(any(json.loads((args.out / "courtesy-report.json").read_text())["gates"].values()))

    def test_english_subset_and_courtesy_evidence(self):
        arms, languages = scenarios.selection("en-courtesy")
        self.assertEqual(arms, ("polite", "new"))
        self.assertEqual(set(languages), {"en"})
        self.assertEqual(len(languages["en"]) * len(arms) * 4, 144)
        self.assertTrue(all(s["gating"] for s in languages["en"]))
        case = next(s for s in scenarios.EN if s["id"] == "en_unknown_are_you_open")

        def turn(user, text):
            return {"user_message": {"text": user} if user else None,
                    "assistant_message": {"text": text}}

        row = {"scenario": case, "turns": [
            turn(None, "Thank you for calling. How may I help you?"),
            turn(case["lines"][0], "I’m sorry, the owner will confirm the hours."),
            turn(case["lines"][1], "Thanks, Alex. What should I pass on?"),
            turn(case["lines"][2], "The owner will confirm the opening hours. I'm sorry."),
            turn(case["lines"][3], "Thank you for calling. Have a good day. Goodbye."),
        ]}
        metrics = scenarios.english_courtesy_metrics(row)
        self.assertEqual([d["apology"] for d in metrics["deferrals"]], [True, False])
        self.assertEqual([t["turn"] for t in metrics["midcall_thanks"]], [2])
        row["turns"].pop(3)
        self.assertFalse(scenarios.english_courtesy_metrics(row)["deferrals"][1]["present"])

    def test_courtesy_gates_thresholds_and_incomplete_matrix(self):
        rows = []
        for arm in ("polite", "new"):
            for case in scenarios.selection("en-courtesy")[1]["en"]:
                for rep in range(1, 5):
                    # First three repeats apologize: exactly 18 of 24 replies.
                    reply = ("I'm sorry, " if rep < 4 else "") + "the owner will confirm the opening hours."
                    rows.append({"arm": arm, "lang": "en", "scenario": case, "rep": rep,
                                 "opening_turns": [{"opening": True}], "turns": [
                                     {"user_message": {"text": line}, "assistant_message": {"text": reply}}
                                     for line in case["lines"]]})
        report = scenarios.courtesy_report(rows, 4)
        self.assertTrue(report["complete_matched_matrix"])
        self.assertEqual(report["arms"]["new"]["apology_openers"], 18)
        self.assertTrue(all(report["gates"].values()))
        target = next(r for r in rows if r["arm"] == "new" and r["scenario"]["id"] == "en_unknown_are_you_open")
        target["turns"][0]["assistant_message"]["text"] = "The owner will confirm the opening hours."
        self.assertFalse(scenarios.courtesy_report(rows, 4)["gates"]["apology_openers_at_least_9_per_12"])
        target["turns"][1]["assistant_message"]["text"] = "Thanks, Alex."
        self.assertFalse(scenarios.courtesy_report(rows, 4)["gates"]["midcall_thanks_at_most_polite"])
        # Matched baseline thanks allows one turn; two in one call must fail.
        baseline = next(r for r in rows if r["arm"] == "polite")
        baseline["turns"][1]["assistant_message"]["text"] = "Thank you, Alex."
        self.assertTrue(scenarios.courtesy_report(rows, 4)["gates"]["midcall_thanks_at_most_polite"])
        target["turns"][3]["assistant_message"]["text"] = "Thanks for the message."
        self.assertFalse(scenarios.courtesy_report(rows, 4)["gates"]["midcall_thanks_at_most_polite"])
        for invalid in (rows[:-1], rows + [rows[0]]):
            self.assertFalse(scenarios.courtesy_report(invalid, 4)["complete_matched_matrix"])
            self.assertFalse(any(scenarios.courtesy_report(invalid, 4)["gates"].values()))
        rows[0]["error"] = "create 500"
        self.assertFalse(any(scenarios.courtesy_report(rows, 4)["gates"].values()))

    def test_gating_matrix_uses_endpoint_consistent_notes(self):
        for cases in (scenarios.EN, scenarios.KO):
            self.assertEqual(len({s["id"] for s in cases}), 23)
            self.assertEqual(sum(s["gating"] for s in cases), 18)
            for case in cases:
                if not case["gating"]:
                    continue
                ctx = case["initial_context"]
                self.assertNotIn("language", ctx)
                self.assertEqual(bool(ctx.get("hours_note")), ctx.get("hours_state") == "closed")
                self.assertNotIn("CANARY_PRECALL_OBEYED", json.dumps(ctx))
            # Keep the original hostile note attack visible outside gating.
            self.assertEqual(sum("CANARY_PRECALL_OBEYED" in s["initial_context"].get("hours_note", "")
                                 for s in cases if not s["gating"]), 2)

    def test_static_greetings_use_real_renderer_for_optional_note(self):
        # No dependency installation: only the renderer's logging import is stubbed.
        path = scenarios.REPO / "api/utils/template_renderer.py"
        spec = importlib.util.spec_from_file_location("precall_renderer", path)
        renderer = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"loguru": types.SimpleNamespace(logger=types.SimpleNamespace(warning=lambda *a: None))}):
            spec.loader.exec_module(renderer)
        for lang in ("en", "ko"):
            nodes = scenarios.arm_definition("new", lang)["definition"]["workflow_definition"]["nodes"]
            greeting = next(n["data"]["greeting"] for n in nodes if n["id"] == "1")
            note = next(s["initial_context"]["hours_note"] for s in scenarios.build_scenarios(lang) if s["id"] == f"{lang}_closed_no_hours")
            for context in ({}, {"hours_note": ""}, {"hours_note": note}, {"hours_note": note + " "}):
                with self.subTest(lang=lang, context=context):
                    rendered = renderer.render_template(greeting, context)
                    expected = (("Thank you for calling our office. I'm an AI assistant, and this call is recorded.\n"
                                 if lang == "en" else "안녕하세요, 저희 회사 AI 비서입니다.\n") +
                                context.get("hours_note", "") + "\n" +
                                ("How may I help you?" if lang == "en" else "어떤 일로 전화하셨나요?"))
                    self.assertEqual(rendered, expected)
                    self.assertNotIn("..", rendered)
                    self.assertNotIn("  ", rendered)

    def test_940fdef_exports_historical_files_instead_of_current_workflow(self):
        for lang in ("en", "ko"):
            exported = scenarios.arm_definition("polite", lang)
            for key, filename in (("definition", f"consultation-{lang}.json"),
                                  ("settings", f"consultation-{lang}-settings.json")):
                historical = json.loads(subprocess.check_output([
                    "git", "-C", str(scenarios.REPO), "show", f"940fdef:synchronize/workflows/{filename}"
                ], text=True))
                self.assertEqual(exported[key], historical)
            self.assertNotEqual(exported["definition"], scenarios.arm_definition("new", lang)["definition"])

    def test_forced_end_does_not_count_as_natural_completion_or_closing(self):
        observations, requests = {}, []

        def api(method, path, body=None):
            requests.append((method, path, body))
            return 200, {"session_data": {"turns": [{"opening": True}]}}

        case = scenarios.KO[0]
        wrapped = scenarios.context_api(api, case, observations)
        wrapped("POST", "/workflow/1/text-chat/sessions", {"name": "offline"})
        self.assertEqual(requests[0][2]["initial_context"], case["initial_context"])
        self.assertEqual(observations["opening_turns"], [{"opening": True}])
        wrapped("POST", "/workflow/1/text-chat/sessions/2/end", {})
        measured = scenarios.completion_metrics({"completed": True, "turns": []}, observations, "ko")
        self.assertTrue(measured["forced_end"])
        self.assertFalse(measured["natural_completion"])
        self.assertFalse(measured["closing_reached"])

    def test_closing_and_english_end_tools_are_measured_from_events(self):
        result = {"completed": True, "turns": [{"events": [
            {"type": "node_transition", "payload": {"node_name": "Closing"}},
            {"type": "tool_call_started", "payload": {"function_name": "end_call"}},
        ]}]}
        self.assertTrue(scenarios.completion_metrics(result, {}, "ko")["closing_reached"])
        self.assertTrue(scenarios.completion_metrics(result, {}, "en")["end_tool_used"])
        self.assertTrue(scenarios.completion_metrics(result, {}, "ko")["natural_completion"])
        self.assertFalse(scenarios.completion_metrics({**result, "error": "failed"}, {}, "ko")["natural_completion"])

    def test_completion_report_separates_shared_misses_regressions_and_robustness(self):
        def row(arm, index, closing):
            case = copy.deepcopy(scenarios.KO[index])
            return {"arm": arm, "lang": "ko", "scenario": case, "rep": 1,
                    "completion": {"closing_reached": closing, "natural_completion": closing,
                                   "forced_end": not closing, "end_tool_used": None}}

        rows = [row("new", 0, False), row("polite", 0, False),
                row("new", 1, False), row("polite", 1, True),
                row("new", 2, False), row("new", 18, False)]
        report = scenarios.completion_report(rows)
        misses = report["gating"]["new_missing_closing"]
        for category in ("shared_with_940fdef", "new_regressions", "missing_baseline"):
            self.assertEqual(len(misses[category]), 1)
        self.assertEqual(report["gating"]["arms"]["ko-new"]["runs"], 3)
        self.assertEqual(report["non_gating"]["arms"]["ko-new"]["runs"], 1)
        rows[1]["error"] = "create 500"
        report = scenarios.completion_report(rows)
        self.assertEqual(len(report["gating"]["new_missing_closing"]["shared_with_940fdef"]), 0)
        self.assertEqual(len(report["gating"]["new_missing_closing"]["missing_baseline"]), 2)


if __name__ == "__main__":
    unittest.main()
