"""Offline checks for the proof's input boundary and completion evidence."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

import scenarios_precall as scenarios


class PrecallProofTests(unittest.TestCase):
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
            exported = scenarios.arm_definition("baseline940fdef", lang)
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

        rows = [row("new", 0, False), row("baseline940fdef", 0, False),
                row("new", 1, False), row("baseline940fdef", 1, True),
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
