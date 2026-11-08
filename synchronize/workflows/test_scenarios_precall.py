"""Offline coverage for the remaining pre-call name and completion proof."""

import importlib.util
import json
from pathlib import Path
import unittest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "scenarios_precall", HERE / "scenarios_precall.py"
)
scenarios = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scenarios)


class PreCallScenariosTest(unittest.TestCase):
    def test_scenarios_send_only_the_business_name(self):
        for lang, cases in (("en", scenarios.EN), ("ko", scenarios.KO)):
            self.assertEqual(len(cases), 8)
            self.assertEqual(len({case["id"] for case in cases}), len(cases))
            for case in cases:
                self.assertLessEqual(set(case["initial_context"]), {"business_name"})
                self.assertEqual(case["expected"]["conversation_language"], lang)
                self.assertTrue(case["expected"]["callback_preference_only"])
            generic = [
                case
                for case in cases
                if case["id"].split("_", 2)[1] in ("absent", "empty")
            ]
            self.assertTrue(all(case["expected"]["generic_name"] for case in generic))

    def test_workflow_templates_keep_the_exact_two_line_greetings(self):
        expected = {
            "en": (
                "Thank you for calling {{business_name | fallback:our office}}. "
                "I'm an AI assistant, and this call is recorded.\nHow may I help you?"
            ),
            "ko": (
                "안녕하세요, {{business_name | fallback:저희 회사}} AI 비서입니다. "
                "통화 내용은 녹음되어 담당자에게 전달됩니다.\n"
                "어떤 일로 전화하셨나요?"
            ),
        }
        for lang in ("en", "ko"):
            definition = json.loads((HERE / f"consultation-{lang}.json").read_text())
            nodes = {
                node["id"]: node for node in definition["workflow_definition"]["nodes"]
            }
            self.assertEqual(nodes["1"]["data"]["greeting"], expected[lang])

    def test_context_api_injects_the_fixture_and_records_the_opening(self):
        calls = []

        def api(method, path, body=None, **kwargs):
            calls.append((method, path, body, kwargs))
            return 200, {"session_data": {"turns": [{"assistant_message": {}}]}}

        observations = {}
        request = scenarios.context_api(
            api, {"initial_context": {"business_name": "Acme"}}, observations
        )
        request("POST", "/text-chat/sessions", {"workflow_id": 3})
        self.assertEqual(
            calls[0][2],
            {"workflow_id": 3, "initial_context": {"business_name": "Acme"}},
        )
        self.assertEqual(len(observations["opening_turns"]), 1)
        request("POST", "/text-chat/sessions/1/end")
        self.assertTrue(observations["forced_end"])

    def test_completion_metrics_distinguish_natural_and_forced_endings(self):
        result = {
            "completed": True,
            "turns": [
                {
                    "events": [
                        {
                            "type": "node_transition",
                            "payload": {"node_name": "Closing"},
                        },
                        {
                            "type": "tool_call_started",
                            "payload": {"function_name": "end_call"},
                        },
                    ]
                }
            ],
        }
        self.assertTrue(
            scenarios.completion_metrics(result, {}, "ko")["closing_reached"]
        )
        self.assertTrue(
            scenarios.completion_metrics(result, {}, "en")["end_tool_used"]
        )
        self.assertFalse(
            scenarios.completion_metrics(
                result, {"forced_end": True}, "en"
            )["natural_completion"]
        )

    def test_default_is_five_repeats(self):
        self.assertEqual(scenarios.DEFAULT_REPEATS, 5)
        report = scenarios.completion_report([])
        _, languages = scenarios.selection()
        self.assertEqual(
            report["expected_runs"],
            5 * len(scenarios.ARM_REFS) * sum(map(len, languages.values())),
        )

    def test_completion_report_fails_on_missing_evidence(self):
        rows = []
        arms, languages = scenarios.selection()
        for lang, cases in languages.items():
            for case in cases:
                for arm in arms:
                    rows.append(
                        {
                            "lang": lang,
                            "arm": arm,
                            "scenario": case,
                            "rep": 1,
                            "opening_turns": [{}],
                            "turns": [{}],
                            "completion": {
                                "natural_completion": True,
                                "forced_end": False,
                                "closing_reached": lang == "ko",
                                "end_tool_used": lang == "en",
                            },
                        }
                    )
        report = scenarios.completion_report(rows, repeats=1)
        self.assertTrue(report["complete"])
        self.assertEqual(report["runs"], report["expected_runs"])
        self.assertFalse(scenarios.completion_report(rows[:-1], repeats=1)["complete"])

    def test_current_arm_exports_the_local_templates(self):
        exported = scenarios.arm_definition("new", "en")
        self.assertEqual(exported["source_ref"], "working-tree")
        self.assertEqual(
            exported["definition"],
            json.loads((HERE / "consultation-en.json").read_text()),
        )


if __name__ == "__main__":
    unittest.main()
