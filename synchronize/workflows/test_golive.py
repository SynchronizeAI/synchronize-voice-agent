"""Targeted offline integration proof: every HTTP request goes to a local fake."""

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


def wire(definition, runtime, credential_uuid):
    indexed = golive.nodes(definition)
    indexed["1"]["data"].update(
        pre_call_fetch_mode="inbound",
        pre_call_fetch_url=runtime + "/api/voice/pre-call",
        pre_call_fetch_credential_uuid=credential_uuid,
    )
    indexed["6"]["data"].update(
        endpoint_url=runtime + "/api/voice/call-ended", credential_uuid=credential_uuid
    )


class FakeDograh(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.runtime = f"http://127.0.0.1:{self.server_port}"
        self.api_key, self.secret = uuid4().hex, uuid4().hex
        self.credential_uuid = str(uuid4())
        self.credentials, self.calls, self.published = [], [], 0
        self.wrong_status = 401
        self.probe_result = {}
        self.credential_error = False
        self.drift = None
        self.redirect = False
        self.on_get = None
        self.select("en")

    def select(self, lang):
        self.lang, self.workflow_id = lang, 3 if lang == "en" else 1
        self.old, self.new = (
            golive.template("940fdef", lang),
            golive.template("HEAD", lang),
        )
        live = copy.deepcopy(self.old)
        # Unchanged field, changed field's extra line, renamed node, custom node,
        # model binding, dictionary and sparse settings all need to survive.
        indexed = golive.nodes(live)
        indexed["1"]["data"]["operator_note"] = "Keep this local tuning"
        indexed["4"]["data"]["name"] = "Our customised persona"
        indexed["4"]["data"]["prompt"] += "\nLOCAL CUSTOM LINE: keep this exactly."
        indexed["1"]["position"] = {"x": 193, "y": 42}
        live["viewport"] = {"x": 17, "y": 19, "zoom": 0.7}
        live["nodes"].append(
            {
                "id": "99",
                "type": "agentNode",
                "data": {"name": "Local note", "prompt": "Keep"},
            }
        )
        self.workflow = {
            "id": self.workflow_id,
            "workflow_definition": live,
            "workflow_configurations": {
                "model_configuration_override": {
                    "model_configuration_uuid": str(uuid4())
                },
                "dictionary": "Custom business",
                "smart_turn_stop_secs": 1.3,
                "custom_setting": {"nested": [True, 7]},
            },
            "template_context_variables": {"local": "value"},
            "version_status": "published",
        }
        self.original = copy.deepcopy(self.workflow)

    def expected(self):
        # Independent oracle: replace changed template fields, keep all local
        # additions. No production patch/diff functions determine expected text.
        out = copy.deepcopy(self.original["workflow_definition"])
        for old in self.old["nodes"]:
            new = next(n for n in self.new["nodes"] if n["id"] == old["id"])
            node = next(n for n in out["nodes"] if n["id"] == old["id"])
            for field in old["data"].keys() | new["data"].keys():
                if old["data"].get(field) != new["data"].get(field):
                    node["data"][field] = copy.deepcopy(new["data"][field])
                    if old["id"] == "4" and field == "prompt":
                        node["data"][field] += "\nLOCAL CUSTOM LINE: keep this exactly."
        out["nodes"].append(
            copy.deepcopy(next(n for n in self.new["nodes"] if n["id"] == "6"))
        )
        wire(out, self.runtime, self.credential_uuid)
        return out


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
        if self.path == "/api/voice/pre-call":
            valid = self.headers.get("X-Dograh-Secret") == fake.secret
            self.reply(
                200 if valid else fake.wrong_status,
                fake.probe_result if valid else {"echo": fake.secret},
            )
            return
        if self.headers.get("X-API-Key") != fake.api_key:
            self.reply(403, {})
            return
        if fake.redirect:
            self.send_response(307)
            self.send_header("Location", fake.runtime + "/redirect-target")
            self.end_headers()
            return
        fetch = f"/api/v1/workflow/fetch/{fake.workflow_id}"
        if self.command == "GET" and self.path == fetch:
            if fake.on_get:
                fake.on_get()
            self.reply(200, fake.workflow)
        elif self.command == "GET" and self.path == "/api/v1/credentials/":
            self.reply(
                200,
                [
                    {k: v for k, v in c.items() if k != "credential_data"}
                    for c in fake.credentials
                ],
            )
        elif self.command in ("POST", "PUT") and self.path.startswith(
            "/api/v1/credentials/"
        ):
            if fake.credential_error:
                self.reply(500, {"detail": body})  # Intentionally echoes secret.
                return
            if self.command == "POST":
                fake.credentials.append({"uuid": fake.credential_uuid, **body})
            else:
                fake.credentials[0].update(body)
            self.reply(
                200,
                {
                    k: v
                    for k, v in fake.credentials[0].items()
                    if k != "credential_data"
                },
            )
        elif (
            self.command == "PUT"
            and self.path == f"/api/v1/workflow/{fake.workflow_id}"
        ):
            fake.workflow.update(copy.deepcopy(body))
            fake.workflow["version_status"] = "draft"
            self.reply(200, fake.workflow)
        elif (
            self.command == "POST"
            and self.path == f"/api/v1/workflow/{fake.workflow_id}/publish"
        ):
            fake.published += 1
            fake.workflow["version_status"] = "published"
            if fake.drift:
                fake.drift(fake.workflow)
            self.reply(200, fake.workflow)
        else:
            self.reply(404, {})

    def reply(self, status, body):
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class GoLiveTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDograh()
        self.thread = threading.Thread(target=self.fake.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.temp = tempfile.TemporaryDirectory(prefix="golive-proof-")
        self.addCleanup(self.temp.cleanup)
        self.backups = Path(self.temp.name) / "backups"
        self.env = patch.dict(
            os.environ,
            {
                "DOGRAH_PROOF_BASE_URL": self.fake.runtime,
                "DOGRAH_PROOF_API_KEY": self.fake.api_key,
                "DOGRAH_WEBHOOK_SECRET": self.fake.secret,
            },
            clear=True,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def stop_server(self):
        self.fake.shutdown()
        self.fake.server_close()
        self.thread.join()

    def args(self, *extra):
        return [
            "--workflow-id",
            str(self.fake.workflow_id),
            "--lang",
            self.fake.lang,
            "--runtime-origin",
            self.fake.runtime,
            "--credential-name",
            "Synchronize reception",
            "--backup-dir",
            str(self.backups),
            *extra,
        ]

    def run_cli(self, args, expected=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = golive.main(args)
        output = stdout.getvalue() + stderr.getvalue()
        # Never include these values in unittest assertion failure messages.
        for secret in (
            self.fake.secret,
            self.fake.api_key,
            os.environ.get("DOGRAH_WEBHOOK_SECRET"),
        ):
            self.assertFalse(
                bool(secret and secret in output), "Secret leaked to output"
            )
        for path in Path(self.temp.name).rglob("*"):
            if path.is_file():
                content = path.read_text()
                for secret in (
                    self.fake.secret,
                    self.fake.api_key,
                    os.environ.get("DOGRAH_WEBHOOK_SECRET"),
                ):
                    self.assertFalse(
                        bool(secret and secret in content), "Secret persisted to file"
                    )
        self.assertEqual(result, expected, output)
        return stdout.getvalue(), stderr.getvalue()

    def writes(self):
        return [call for call in self.fake.calls if call[0] != "GET"]

    def test_plan_read_only_fresh_private_backup_and_field_diff(self):
        out, err = self.run_cli(["plan", *self.args()])
        self.assertEqual(err, "")
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.fake.workflow, self.fake.original)
        self.assertIn("node 4.data.prompt", out)
        self.assertIn("changed lines", out)
        self.assertIn("Would create/update credential (secret not shown)", out)
        self.assertNotIn(self.fake.runtime, out)
        self.assertNotIn(self.fake.new["nodes"][1]["data"]["prompt"], out)
        (path,) = self.backups.glob("*.json")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        saved = json.loads(path.read_text())
        self.assertEqual(
            saved["workflow_definition"], self.fake.original["workflow_definition"]
        )
        self.assertEqual(
            saved["workflow_configurations"],
            self.fake.original["workflow_configurations"],
        )
        self.assertIn(str(path), out)

    def test_apply_both_languages_exact_changes_and_create_then_update(self):
        for lang in ("en", "ko"):
            with self.subTest(lang=lang):
                self.fake.select(lang)
                self.fake.credentials.clear()
                self.fake.calls.clear()
                expected = self.fake.expected()
                self.run_cli(self.args("--apply"))
                self.assertEqual(self.fake.workflow["workflow_definition"], expected)
                for key in golive.SETTINGS:
                    self.assertEqual(self.fake.workflow[key], self.fake.original[key])
                self.assertEqual([c[0] for c in self.writes()], ["POST", "PUT", "POST"])
                self.assertEqual(set(self.writes()[1][2]), {"workflow_definition"})
                first = self.fake.credentials[0]["credential_data"]
                self.assertTrue(
                    first
                    == {
                        "header_name": "X-Dograh-Secret",
                        "header_value": self.fake.secret,
                    }
                )
                self.fake.calls.clear()
                os.environ["DOGRAH_WEBHOOK_SECRET"] = uuid4().hex
                self.run_cli(self.args("--apply"))
                self.assertEqual(self.fake.workflow["workflow_definition"], expected)
                self.assertEqual([c[0] for c in self.writes()], ["PUT", "PUT", "POST"])
                self.assertEqual(len(self.fake.credentials), 1)
                self.assertTrue(
                    self.fake.credentials[0]["credential_data"]["header_value"]
                    == os.environ["DOGRAH_WEBHOOK_SECRET"]
                )
                os.environ["DOGRAH_WEBHOOK_SECRET"] = self.fake.secret

    def test_no_match_or_duplicate_aborts_before_any_write_with_field(self):
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate):
                self.fake.select("en")
                self.fake.calls.clear()
                data = self.fake.workflow["workflow_definition"]["nodes"][0]["data"]
                data["greeting"] = (
                    data["greeting"] * 2 if duplicate else "An incompatible greeting"
                )
                _, err = self.run_cli(self.args("--apply"), expected=1)
                self.assertIn("node 1.data.greeting", err)
                self.assertIn("exactly once", err)
                self.assertEqual(self.writes(), [])

    def test_existing_webhook_only_two_fields_change(self):
        live = self.fake.workflow["workflow_definition"]
        webhook = copy.deepcopy(
            next(n for n in self.fake.new["nodes"] if n["id"] == "6")
        )
        webhook["data"].update(
            endpoint_url=self.fake.runtime + "/old",
            credential_uuid=str(uuid4()),
            name="Local completion name",
            payload_template={"workflow_run_id": "{{workflow_run_id}}", "custom": True},
        )
        webhook["position"] = {"x": 813, "y": 17}
        live["nodes"].append(webhook)
        original = copy.deepcopy(webhook)
        self.run_cli(self.args("--apply"))
        result = golive.nodes(self.fake.workflow["workflow_definition"])["6"]
        original["data"].update(
            endpoint_url=self.fake.runtime + "/api/voice/call-ended",
            credential_uuid=self.fake.credential_uuid,
        )
        self.assertEqual(result, original)

    def test_rollback_restores_byte_equal_definition_and_every_setting(self):
        original_bytes = json.dumps(
            self.fake.original["workflow_definition"], ensure_ascii=False
        ).encode()
        self.run_cli(self.args("--apply"))
        (path,) = self.backups.glob("*.json")
        self.fake.workflow["workflow_configurations"] = {"dictionary": "changed"}
        self.fake.workflow["template_context_variables"] = {"local": "changed"}
        self.run_cli(["--rollback", str(path)])
        self.assertEqual(
            json.dumps(
                self.fake.workflow["workflow_definition"], ensure_ascii=False
            ).encode(),
            original_bytes,
        )
        for key in golive.SETTINGS:
            self.assertEqual(self.fake.workflow[key], self.fake.original[key])
        self.assertEqual(len(list(self.backups.glob("*.json"))), 2)
        self.assertEqual(len(self.fake.credentials), 1)
        self.assertEqual(self.fake.published, 2)

    def test_placeholder_origins_targets_and_connection_injection_rejected(self):
        for bad in (
            "https://synchronize.example.invalid",
            "https://<RUNTIME>",
            "https://placeholder.test",
            "https://...",
            "https://RUNTIME_ORIGIN",
            "https://REPLACE-ME",
            "https://your-runtime.test",
            "http://public.test",
            "https://real.test/path",
            "https://real.test?secret=a",
            "https://user:password@real.test",
        ):
            args = self.args()
            args[args.index("--runtime-origin") + 1] = bad
            self.run_cli(args, expected=1)
        for name in ("DOGRAH_PROOF_BASE_URL", "DOGRAH_PROOF_API_KEY"):
            for suffix in ('"', "'", "\r", "\n"):
                with patch.dict(os.environ, {name: os.environ[name] + suffix}):
                    self.run_cli(self.args(), expected=1)
        for workflow_id, lang in ((2, "ko"), (1, "en"), (3, "ko")):
            self.run_cli(
                [*self.args(), "--workflow-id", str(workflow_id), "--lang", lang],
                expected=1,
            )
        self.assertEqual(self.fake.calls, [])

    def test_backup_failure_missing_secret_and_unresolved_placeholder_block_writes(
        self,
    ):
        with patch.dict(os.environ, {"DOGRAH_WEBHOOK_SECRET": ""}):
            self.run_cli(self.args("--apply"), expected=1)
        self.run_cli(
            [
                *self.args("--apply"),
                "--backup-dir",
                str(golive.ROOT / "backup-not-allowed"),
            ],
            expected=1,
        )
        with patch.object(golive.os, "open", side_effect=OSError("disk full")):
            self.run_cli(self.args("--apply"), expected=1)
        self.fake.workflow["workflow_definition"]["extra"] = "<DOGRAH_UNRESOLVED>"
        _, err = self.run_cli(self.args("--apply"), expected=1)
        self.assertIn("placeholder", err)
        self.assertEqual(self.writes(), [])

    def test_echoed_secret_error_is_suppressed_and_backup_has_no_secret(self):
        self.fake.credential_error = True
        _, err = self.run_cli(self.args("--apply"), expected=1)
        self.assertIn("HTTP 500", err)
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(self.fake.published, 0)
        self.fake.workflow["workflow_definition"]["unexpected_secret"] = (
            self.fake.secret
        )
        self.run_cli(self.args(), expected=1)
        self.assertEqual(len(list(self.backups.glob("*.json"))), 1)
        # Escaped JSON still contains a secret semantically. Refuse it before
        # writing, even when its literal spelling would not match JSON bytes.
        os.environ["DOGRAH_WEBHOOK_SECRET"] = "not-a-real-secret\\escaped"
        self.fake.workflow["workflow_definition"]["unexpected_secret"] = os.environ[
            "DOGRAH_WEBHOOK_SECRET"
        ]
        self.run_cli(self.args(), expected=1)
        self.assertEqual(len(list(self.backups.glob("*.json"))), 1)

    def test_published_definition_settings_and_model_drift_fail_verification(self):
        def change_definition(workflow):
            workflow["workflow_definition"]["nodes"].pop()

        def change_settings(workflow):
            workflow["workflow_configurations"]["dictionary"] = "drift"

        def change_model(workflow):
            workflow["workflow_configurations"]["model_configuration_override"] = {}

        for drift in (change_definition, change_settings, change_model):
            self.fake.select("en")
            self.fake.drift = drift
            self.run_cli(self.args("--apply"), expected=1)
        self.assertEqual(self.fake.published, 3)

    def test_concurrent_edit_and_redirect_do_not_write(self):
        gets = 0

        def on_get():
            nonlocal gets
            gets += 1
            if gets == 2:
                self.fake.workflow["workflow_configurations"]["dictionary"] = (
                    "Concurrent edit"
                )

        self.fake.on_get = on_get
        self.run_cli(self.args("--apply"), expected=1)
        self.assertEqual(self.writes(), [])
        self.fake.redirect = True
        self.run_cli(self.args(), expected=1)
        self.assertFalse(any(c[1] == "/redirect-target" for c in self.fake.calls))

    def test_probe_codes_body_and_negative_auth_failures(self):
        args = [
            "probe",
            "--runtime-origin",
            self.fake.runtime,
            "--to-number",
            "+12025550199",
        ]
        for status in (401, 403):
            self.fake.wrong_status = status
            out, err = self.run_cli(args)
            self.assertEqual(out, f"200\n{status}\n")
            self.assertEqual(err, "")
        for status in (404, 500, 503, 200):
            self.fake.wrong_status = status
            self.run_cli(args, expected=1)
        self.fake.probe_result = []
        self.run_cli(args, expected=1)
        self.assertEqual(
            self.fake.calls[0][2],
            {
                "call_inbound": {
                    "from_number": "+12025550100",
                    "to_number": "+12025550199",
                    "workflow_run_id": "0",
                }
            },
        )
        self.assertTrue(
            all(path == "/api/voice/pre-call" for _, path, _ in self.fake.calls)
        )
        self.assertFalse(self.backups.exists())

    def test_original_word_merge_and_multiline_insert_delete_are_exact_once(self):
        self.assertEqual(
            golive.patch_text(
                "Hello old world", "Hello new world", "Custom Hello old world", "field"
            ),
            "Custom Hello new world",
        )
        self.assertEqual(
            golive.patch_text("A\nB", "HEAD\nA\nB", "A\nB\nLOCAL", "field"),
            "HEAD\nA\nB\nLOCAL",
        )
        self.assertEqual(
            golive.patch_text("A\nB\nC", "A\nC", "A\nB\nC\nLOCAL", "field"),
            "A\nC\nLOCAL",
        )
        with self.assertRaisesRegex(golive.GoLiveError, "field"):
            golive.patch_text("old", "new", "old old", "field")
        with self.assertRaisesRegex(golive.GoLiveError, "field"):
            golive.patch_value([1], [2], [1, 3], "field")
        with self.assertRaisesRegex(golive.GoLiveError, "local git commit"):
            golive.template("--output=must-never-write", "en")


if __name__ == "__main__":
    unittest.main()
