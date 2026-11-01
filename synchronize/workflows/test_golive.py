"""Targeted offline integration proof: every HTTP request goes to a local fake."""

import ast
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


def schema_fields():
    """Read actual DTO field names without loading backend dependencies."""
    classes = {}
    for name in ("dto.py", "node_data.py"):
        tree = ast.parse((golive.ROOT / "api/services/workflow" / name).read_text())
        classes.update({c.name: c for c in tree.body if isinstance(c, ast.ClassDef)})

    def fields(name):
        cls = classes[name]
        return {
            s.target.id
            for s in cls.body
            if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
        } | set().union(
            *(
                fields(b.id)
                for b in cls.bases
                if isinstance(b, ast.Name) and b.id in classes
            )
        )

    return {
        kind: fields(name)
        for kind, name in {
            "startCall": "StartCallNodeData",
            "agentNode": "AgentNodeData",
            "endCall": "EndCallNodeData",
            "globalNode": "GlobalNodeData",
            "webhook": "WebhookNodeData",
            "qa": "QANodeData",
            "edge": "EdgeDataDTO",
        }.items()
    }


ALLOWED_DATA = schema_fields()


def sanitized(definition):
    out = copy.deepcopy(definition)
    for item in out["nodes"] + out["edges"]:
        allowed = ALLOWED_DATA.get(
            item.get("type"), ALLOWED_DATA["edge"] if "source" in item else None
        )
        if allowed is not None and isinstance(item.get("data"), dict):
            item["data"] = {k: v for k, v in item["data"].items() if k in allowed}
    return out


def masked(workflow):
    out = copy.deepcopy(workflow)
    for node in out["workflow_definition"]["nodes"]:
        data = node["data"]
        if node["type"] == "qa" and data.get("qa_api_key"):
            value = data["qa_api_key"]
            data["qa_api_key"] = "*" * (len(value) - 4) + value[-4:]
    return out


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
        self.completion_status, self.completion_wrong_status = 200, 401
        self.completion_result = {}
        self.credential_error = False
        self.drift = None
        self.redirect = False
        self.on_get = None
        self.failure = None
        self.probe_headers = []
        self.qa_key = "qa-" + uuid4().hex
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
        indexed["1"]["data"]["allow_interrupt"] = False
        for node in live["nodes"]:
            if node["type"] == "qa":
                node["data"]["qa_api_key"] = self.qa_key
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
            },
            "template_context_variables": {"local": "value"},
            "version_status": "published",
            "version_number": 7,
            "current_definition_id": 107,
        }
        self.released = copy.deepcopy(self.workflow)
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
        if fake.failure and fake.failure(self.command, self.path):
            self.reply(409, {"detail": body})
            return
        if self.path in ("/api/voice/pre-call", "/api/voice/call-ended"):
            fake.probe_headers.append(dict(self.headers))
            valid = self.headers.get("X-Dograh-Secret") == fake.secret
            completion = self.path == "/api/voice/call-ended"
            self.reply(
                (fake.completion_status if valid else fake.completion_wrong_status)
                if completion else (200 if valid else fake.wrong_status),
                (fake.completion_result if completion else fake.probe_result)
                if valid else {"echo": fake.secret},
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
            self.reply(200, masked(fake.workflow))
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
            if "workflow_definition" in body:
                body = copy.deepcopy(body)
                body["workflow_definition"] = sanitized(body["workflow_definition"])
                old_nodes = golive.nodes(fake.workflow["workflow_definition"])
                for node in body["workflow_definition"]["nodes"]:
                    if node["type"] == "qa":
                        old_key = (
                            old_nodes.get(node["id"], {})
                            .get("data", {})
                            .get("qa_api_key")
                        )
                        if (
                            old_key
                            and node["data"].get("qa_api_key")
                            == "*" * (len(old_key) - 4) + old_key[-4:]
                        ):
                            node["data"]["qa_api_key"] = old_key
            if fake.workflow["version_status"] != "draft":
                fake.workflow["version_number"] += 1
            fake.workflow.update(copy.deepcopy(body))
            fake.workflow["version_status"] = "draft"
            self.reply(200, masked(fake.workflow))
        elif (
            self.command == "POST"
            and self.path == f"/api/v1/workflow/{fake.workflow_id}/publish"
        ):
            fake.published += 1
            fake.workflow["version_status"] = "published"
            fake.workflow["current_definition_id"] += 1
            if fake.drift:
                fake.drift(fake.workflow)
            fake.released = copy.deepcopy(fake.workflow)
            self.reply(
                200,
                {
                    "id": fake.workflow["current_definition_id"],
                    "version_number": fake.workflow["version_number"],
                    "status": "published",
                    "published_at": "2026-11-01T00:00:00Z",
                    "version_name": None,
                    "change_description": None,
                },
            )
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

    def args(self, *extra, accept_merged=("node 4.data.prompt",)):
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
            *(arg for field in accept_merged for arg in ("--accept-merged", field)),
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
        self.assertEqual(saved["format_version"], 2)
        self.assertEqual(
            saved["workflow_definition"],
            masked(self.fake.original)["workflow_definition"],
        )
        self.assertEqual(
            saved["workflow_configurations"],
            self.fake.original["workflow_configurations"],
        )
        self.assertIn(str(path), out)

    def test_apply_both_languages_exact_changes_and_second_apply_noop(self):
        for lang in ("en", "ko"):
            with self.subTest(lang=lang):
                self.fake.select(lang)
                self.fake.credentials.clear()
                self.fake.calls.clear()
                expected = self.fake.expected()
                out, _ = self.run_cli(self.args("--apply"))
                self.assertIn("ROLLBACK FILE:", out)
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
                out, _ = self.run_cli(self.args("--apply"))
                self.assertIn("ROLLBACK FILE:", out)
                self.assertEqual(self.fake.workflow["workflow_definition"], expected)
                self.assertEqual(self.writes(), [])
                self.assertIn("Already wired", out)
                self.assertEqual(len(self.fake.credentials), 1)
                self.assertTrue(
                    self.fake.credentials[0]["credential_data"]["header_value"]
                    == self.fake.secret
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
            "--workflow-id",
            "3",
        ]
        for status in (401, 403):
            self.fake.wrong_status = status
            self.fake.completion_wrong_status = status
            out, err = self.run_cli(args)
            self.assertEqual(out, f"200\n{status}\n200\n{status}\n")
            self.assertEqual(err, "")
        for status in (404, 500, 503, 200):
            self.fake.wrong_status = status
            self.run_cli(args, expected=1)
        self.fake.probe_result = []
        self.run_cli(args, expected=1)
        self.assertEqual(
            self.fake.calls[0][2],
            {
                "event": "call_inbound",
                "call_inbound": {
                    "agent_id": 3,
                    "from_number": "+12025550100",
                    "to_number": "+12025550199",
                    "workflow_run_id": golive.PROBE_RUN_ID,
                },
            },
        )
        self.assertTrue(all(
            path in ("/api/voice/pre-call", "/api/voice/call-ended")
            for _, path, _ in self.fake.calls
        ))
        self.assertEqual(
            self.fake.probe_headers[0]["Idempotency-Key"],
            f"dograh-pre-call-{golive.PROBE_RUN_ID}",
        )
        self.assertFalse(self.backups.exists())

    def test_call_ended_probe_uses_digit_text_and_rejects_bad_statuses_or_bodies(self):
        args = [
            "probe", "--runtime-origin", self.fake.runtime,
            "--to-number", "+12025550199", "--workflow-id", "3",
        ]
        out, err = self.run_cli(args)
        self.assertEqual(out, "200\n401\n200\n401\n")
        self.assertEqual(err, "")
        self.assertEqual(
            [path for _, path, _ in self.fake.calls],
            ["/api/voice/pre-call"] * 2 + ["/api/voice/call-ended"] * 2,
        )
        for _, _, body in self.fake.calls[2:]:
            self.assertEqual(body, {"workflow_run_id": "2147483647"})
        self.assertTrue(self.fake.probe_headers[2]["X-Dograh-Secret"] == self.fake.secret)
        self.assertTrue(self.fake.probe_headers[3]["X-Dograh-Secret"] != self.fake.secret)
        self.assertNotIn("Idempotency-Key", self.fake.probe_headers[2])
        for status in (200, 404, 422, 500, 503):
            self.fake.completion_wrong_status = status
            _, err = self.run_cli(args, expected=1)
            self.assertIn("Wrong-secret call-ended probe", err)
        self.fake.completion_wrong_status = 401
        for status in (404, 422, 500, 503):
            self.fake.completion_status = status
            _, err = self.run_cli(args, expected=1)
            self.assertIn("Authenticated call-ended probe", err)
        self.fake.completion_status = 200
        for body in (None, [], {"unexpected": True}):
            self.fake.completion_result = body
            self.run_cli(args, expected=1)
        self.assertFalse(self.backups.exists())

    def test_customised_korean_greeting_becomes_three_lines(self):
        old = golive.nodes(golive.template("940fdef", "ko"))["1"]["data"]["greeting"]
        new = golive.nodes(golive.template("HEAD", "ko"))["1"]["data"]["greeting"]
        live = "안녕하세요, 영훈솔루션 AI 비서입니다. 어떤 일로 전화하셨나요?"
        self.assertEqual(
            golive.patch_text(old, new, live, "node 1.data.greeting"),
            "안녕하세요, 영훈솔루션 AI 비서입니다.\n{{hours_note | fallback: }}\n어떤 일로 전화하셨나요?",
        )

    def test_customised_korean_greeting_apply_is_idempotent(self):
        self.fake.select("ko")
        data = golive.nodes(self.fake.workflow["workflow_definition"])["1"]["data"]
        data["greeting"] = (
            "안녕하세요, 영훈솔루션 AI 비서입니다. 어떤 일로 전화하셨나요?"
        )
        out, _ = self.run_cli(self.args("--apply", "--accept-merged", "node 1.data.greeting"))
        self.assertIn("node 1.data.greeting: MERGED-INTO-CUSTOM; -1/+3 changed lines", out)
        first = copy.deepcopy(self.fake.workflow)
        self.fake.calls.clear()
        out, _ = self.run_cli(self.args("--apply"))
        self.assertIn("Already wired", out)
        self.assertEqual(self.writes(), [])
        self.assertEqual(first, self.fake.workflow)

    def test_silent_skips_are_conflicts_with_operator_guidance(self):
        for old, new, live in (
            ("A\nB\nD", "A\nC\nD", "A\nB-custom\nD\nC"),
            ("W\nX\nY", "W\nY", "W\nX-custom\nY"),
            ("hello", "?", "custom question?"),
            (
                "Hi. How may I help you?",
                "Hi. How may I help?",
                "Hi there. How can I help you today?",
            ),
        ):
            with self.subTest(old=old):
                with self.assertRaisesRegex(
                    golive.GoLiveError, "field.*Dograh UI.*re-plan"
                ):
                    golive.patch_text(old, new, live, "field")
        old = golive.nodes(golive.template("940fdef", "ko"))["4"]["data"]["prompt"]
        new = golive.nodes(golive.template("HEAD", "ko"))["4"]["data"]["prompt"]
        live = old.replace("{{business_name | fallback:저희 회사}}", "영훈솔루션")
        with self.assertRaisesRegex(
            golive.GoLiveError, "node 4.data.prompt.*Dograh UI"
        ):
            golive.patch_text(old, new, live, "node 4.data.prompt")

    def test_draft_at_backup_or_fresh_fetch_blocks_all_writes(self):
        for fresh_only in (False, True):
            self.fake.select("en")
            self.fake.calls.clear()
            gets = 0

            def on_get():
                nonlocal gets
                gets += 1
                if not fresh_only or gets == 2:
                    self.fake.workflow["version_status"] = "draft"
                    self.fake.workflow["version_number"] += 1
                    self.fake.workflow["workflow_configurations"][
                        "model_configuration_override"
                    ] = {}

            self.fake.on_get = on_get
            _, err = self.run_cli(self.args("--apply"), expected=1)
            self.assertIn(
                "an unpublished draft exists on workflow 3; publish or discard it in the Dograh UI first",
                err,
            )
            self.assertEqual(self.writes(), [])
            self.assertEqual(self.fake.released, self.fake.original)

    def test_backup_records_published_version_and_definition_id(self):
        out, _ = self.run_cli(self.args())
        (path,) = self.backups.glob("*.json")
        saved = json.loads(path.read_text())
        self.assertEqual(saved["version_number"], 7)
        self.assertEqual(saved["current_definition_id"], 107)
        self.assertEqual(saved["version_status"], "published")
        self.assertIn("published version 7; published definition id 107", out)

    def test_each_failed_stage_reports_recovery_state(self):
        cases = (
            (
                "credential create",
                "POST",
                "/api/v1/credentials/",
                "unchanged",
                "unchanged",
                "no",
            ),
            (
                "credential rotate",
                "PUT",
                "/api/v1/credentials/",
                "unchanged",
                "unchanged",
                "no",
            ),
            ("draft save", "PUT", "/api/v1/workflow/3", "created", "unchanged", "no"),
            (
                "publish",
                "POST",
                "/api/v1/workflow/3/publish",
                "created",
                "unchanged",
                "yes",
            ),
            (
                "read-back",
                "GET",
                "/api/v1/workflow/fetch/3",
                "created",
                "changed",
                "no",
            ),
        )
        for stage, method, path, cred, published, draft in cases:
            with self.subTest(stage=stage):
                self.fake.select("en")
                self.fake.credentials.clear()
                self.fake.published = 0
                if stage == "credential rotate":
                    self.fake.credentials.append(
                        {
                            "uuid": self.fake.credential_uuid,
                            "name": "Synchronize reception",
                            "credential_type": "custom_header",
                        }
                    )
                self.fake.failure = lambda m, p: (
                    m == method
                    and p.startswith(path)
                    and (stage != "read-back" or self.fake.published > 0)
                )
                out, err = self.run_cli(self.args("--apply"), expected=1)
                self.assertEqual(err.count("RECOVERY"), 1)
                self.assertIn(f"failed stage: {stage}", err)
                self.assertIn(f"credential: {cred}", err)
                self.assertIn(f"live published definition: {published}", err)
                self.assertIn(f"draft left behind: {draft}", err)
                backup_path = next(
                    line.removeprefix("ROLLBACK FILE: ")
                    for line in out.splitlines()
                    if line.startswith("ROLLBACK FILE: ")
                )
                if draft != "no" or published != "unchanged":
                    self.assertIn("--rollback " + backup_path, err)
                    self.assertIn("python3 " + str(Path(golive.__file__).resolve()), err)
                else:
                    self.assertNotIn("--rollback", err)
                    self.assertIn("No workflow rollback needed", err)
                if draft == "yes":
                    self.assertIn("Anyone pressing Publish", err)
                    self.assertIn("rolled back or discarded", err)

    def test_transport_failure_after_put_or_publish_is_unknown_may_have_committed(self):
        original_request = golive.request
        for stage in ("draft save", "publish"):
            with self.subTest(stage=stage):
                self.fake.select("en")
                self.fake.credentials.clear()
                self.fake.published = 0

                def uncertain(method, url, headers, body=None):
                    result = original_request(method, url, headers, body)
                    if (
                        stage == "draft save"
                        and method == "PUT"
                        and url.endswith("/workflow/3")
                    ) or (stage == "publish" and url.endswith("/publish")):
                        raise golive.UncertainWrite(
                            "HTTP transport failed; unknown, may have committed"
                        )
                    return result

                with patch.object(golive, "request", side_effect=uncertain):
                    _, err = self.run_cli(self.args("--apply"), expected=1)
                self.assertIn(f"failed stage: {stage}", err)
                self.assertIn("unknown, may have committed", err)
                self.assertIn("draft left behind: unknown", err)
                self.assertIn("Anyone pressing Publish", err)
                if stage == "draft save":
                    self.assertEqual(self.fake.released, self.fake.original)
                    self.assertEqual(self.fake.workflow["version_status"], "draft")
                else:
                    self.assertIn("live published definition: unknown", err)
                    self.assertEqual(self.fake.workflow["version_status"], "published")

    def test_existing_credential_rotates_when_definition_needs_wiring(self):
        self.fake.credentials.append(
            {
                "uuid": self.fake.credential_uuid,
                "name": "Synchronize reception",
                "credential_type": "custom_header",
            }
        )
        out, _ = self.run_cli(self.args("--apply"))
        self.assertIn("Stage: credential rotate", out)
        self.assertEqual([c[0] for c in self.writes()], ["PUT", "PUT", "POST"])
        self.assertTrue(
            self.fake.credentials[0]["credential_data"]["header_value"]
            == self.fake.secret
        )

    def test_sanitizer_and_mask_fidelity(self):
        self.assertEqual(sanitized(self.fake.old), self.fake.old)
        # The fake must remove unknown data keys exactly where Dograh does,
        # while preserving unknown top-level metadata and nested payload values.
        live = copy.deepcopy(self.fake.old)
        live["nodes"][0]["data"]["operator_note"] = "unsupported"
        live["nodes"][0]["local_position"] = 1
        live["edges"] = [
            {"source": "1", "target": "99", "data": {"label": "next", "invalid": True}}
        ]
        cleaned = sanitized(live)
        self.assertNotIn("operator_note", cleaned["nodes"][0]["data"])
        self.assertNotIn("invalid", cleaned["edges"][0]["data"])
        self.assertEqual(cleaned["nodes"][0]["local_position"], 1)
        self.run_cli(self.args("--apply"))
        for path in self.backups.glob("*.json"):
            self.assertFalse(self.fake.qa_key in path.read_text(), "QA key persisted")
        self.assertTrue(
            any(
                n["data"].get("qa_api_key") == self.fake.qa_key
                for n in self.fake.workflow["workflow_definition"]["nodes"]
            )
        )

    def test_rollback_of_failed_publish_diffs_and_omits_unchanged_settings(self):
        self.fake.failure = lambda m, p: p.endswith("/publish")
        self.run_cli(self.args("--apply"), expected=1)
        (path,) = self.backups.glob("*.json")
        self.assertEqual(self.fake.workflow["version_status"], "draft")
        self.fake.failure = None
        self.fake.calls.clear()
        out, _ = self.run_cli(["--rollback", str(path)])
        self.assertIn("overwrites later edits", out)
        self.assertIn("node 1.data.pre_call_fetch_url", out)
        self.assertIn("[removed node]", out)
        self.assertEqual(set(self.writes()[0][2]), {"workflow_definition"})
        self.assertEqual(
            self.fake.workflow["workflow_definition"],
            self.fake.original["workflow_definition"],
        )
        drafts = [p for p in self.backups.glob("*.json") if p != path]
        _, err = self.run_cli(["--rollback", str(drafts[0])], expected=1)
        self.assertIn("Cannot restore a draft backup", err)

    def test_default_enabled_durable_backup_origin_and_unicode_hints(self):
        webhook = copy.deepcopy(golive.nodes(self.fake.new)["6"])
        webhook["data"].pop("enabled")
        self.fake.workflow["workflow_definition"]["nodes"].append(webhook)
        self.run_cli(self.args("--apply"))
        self.assertNotIn(
            "enabled",
            golive.nodes(self.fake.workflow["workflow_definition"])["6"]["data"],
        )
        for path in (
            "/tmp",
            "/private/tmp",
            "/tmp/golive-backups",
            "/private/tmp/golive-backups",
        ):
            with self.assertRaisesRegex(golive.GoLiveError, "durable"):
                golive.backup_directory(Path(path))
        self.assertEqual(
            golive.origin("https://exampleclinic.com"), "https://exampleclinic.com"
        )
        for value in ("https://bad.invalid", "https://host:bad"):
            with self.assertRaisesRegex(golive.GoLiveError, "DOGRAH_PROOF_BASE_URL"):
                golive.origin(value, "DOGRAH_PROOF_BASE_URL")
        for live in ("A\r\nB\r\nC", "A\n한\nC"):
            with self.assertRaisesRegex(golive.GoLiveError, "LF/CRLF.*NFC/NFD"):
                golive.patch_text("A\n한\nC", "A\n새\nC", live, "field")

    def test_two_context_lines_disambiguate_insert_delete(self):
        self.assertEqual(
            golive.patch_text(
                "A\nB\nC\nD", "A\nB\nX\nC\nD", "C\nLOCAL\nA\nB\nC\nD", "field"
            ),
            "C\nLOCAL\nA\nB\nX\nC\nD",
        )
        self.assertEqual(
            golive.patch_text(
                "A\nB\nX\nC\nD", "A\nB\nC\nD", "X\nC\nLOCAL\nA\nB\nX\nC\nD", "field"
            ),
            "X\nC\nLOCAL\nA\nB\nC\nD",
        )

    def test_original_word_merge_and_multiline_insert_delete_are_exact_once(self):
        # Character merging is now limited to single-line -> multiline fields.
        with self.assertRaisesRegex(golive.GoLiveError, "field"):
            golive.patch_text(
                "Hello old world", "Hello new world", "Custom Hello old world", "field"
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

    def test_unrelated_character_fallback_reproductions_abort(self):
        for old, new, live in (
            ("Intro\nSay no.", "Intro\nSay yes.", "Intro\nSay it.\nWe say no."),
            (
                "Rules\nKeep it short.\nEnd",
                "Rules\nKeep it short and kind.\nEnd",
                "Rules custom\nBe brief.\nEnd custom\n...",
            ),
            ("...help you?", "...help you today?", "Hello, what do you need?"),
        ):
            with self.subTest(old=old):
                with self.assertRaisesRegex(golive.GoLiveError, "field.*Dograh UI.*re-plan"):
                    golive.patch_text(old, new, live, "field")

    def test_greeting_requires_both_substantial_word_boundary_anchors(self):
        for old, new, live in (
            ("Left side has words?", "Left side has words\nNew line?", "Custom Left side has words?"),
            ("Hi. Right side has words", "Hi.\nRight side has words", "Custom Hi. Right side has words"),
            ("Substantialprefix right side words", "Substantialpre\nfix right side words", "Custom Substantialprefix right side words"),
        ):
            with self.subTest(old=old):
                with self.assertRaisesRegex(golive.GoLiveError, "field"):
                    golive.patch_text(old, new, live, "field")

    def test_merged_fields_need_explicit_repeatable_acceptance_before_writes(self):
        self.fake.select("ko")
        golive.nodes(self.fake.workflow["workflow_definition"])["1"]["data"]["greeting"] = (
            "안녕하세요, 영훈솔루션 AI 비서입니다. 어떤 일로 전화하셨나요?"
        )
        out, _ = self.run_cli(self.args(accept_merged=()))
        self.assertIn("node 1.data.greeting: MERGED-INTO-CUSTOM; -1/+3 changed lines", out)
        self.assertIn("node 4.data.prompt: MERGED-INTO-CUSTOM;", out)
        self.assertIn("+{{hours_note | fallback: }}", out)
        self.assertEqual(self.writes(), [])
        for accepted in ((), ("node 1.data.greeting",), ("node 4.data.prompt",)):
            _, err = self.run_cli(self.args("--apply", accept_merged=accepted), expected=1)
            self.assertIn("Review the MERGED-INTO-CUSTOM diff", err)
            self.assertIn("--accept-merged", err)
            self.assertEqual(self.writes(), [])
        self.run_cli(self.args("--apply", accept_merged=("typo",)), expected=1)
        self.assertEqual(self.writes(), [])
        self.run_cli(self.args("--apply", accept_merged=("node 1.data.greeting", "node 4.data.prompt")))
        self.assertEqual([c[0] for c in self.writes()], ["POST", "PUT", "POST"])

    def test_template_replaced_fields_are_labelled_and_need_no_acceptance(self):
        golive.nodes(self.fake.workflow["workflow_definition"])["4"]["data"]["prompt"] = (
            golive.nodes(self.fake.old)["4"]["data"]["prompt"]
        )
        out, _ = self.run_cli(self.args("--apply", accept_merged=()))
        self.assertIn("node 1.data.greeting: template-replaced; -1/+3 changed lines", out)
        self.assertIn("node 4.data.prompt: template-replaced;", out)
        self.assertNotIn("MERGED-INTO-CUSTOM", out)

    def test_malformed_workflow_responses_are_safe_stage_failures(self):
        original_request = golive.request
        for stage in ("backup", "fresh fetch", "read-back"):
            for malformed in (None, [], "not an object", 7):
                with self.subTest(stage=stage, malformed=malformed):
                    self.fake.select("en")
                    self.fake.credentials.clear()
                    self.fake.calls.clear()
                    gets = 0

                    def malformed_response(method, url, headers, body=None):
                        nonlocal gets
                        result = original_request(method, url, headers, body)
                        if method == "GET" and "/workflow/fetch/" in url:
                            gets += 1
                            if gets == {"backup": 1, "fresh fetch": 2, "read-back": 3}[stage]:
                                return 200, malformed
                        return result

                    with patch.object(golive, "request", side_effect=malformed_response):
                        _, err = self.run_cli(self.args("--apply"), expected=1)
                    self.assertIn("malformed response", err)
                    self.assertNotIn("Traceback", err)
                    if stage == "read-back":
                        self.assertEqual(err.count("RECOVERY"), 1)
                        self.assertIn("failed stage: read-back", err)
                        self.assertIn("live published definition: changed", err)
                        self.assertIn("--rollback", err)
                    else:
                        self.assertNotIn("RECOVERY", err)
                        self.assertEqual(self.writes(), [])

    def test_attribute_error_after_write_is_suppressed_with_recovery(self):
        with patch.object(golive, "verify_snapshot", side_effect=AttributeError("private details")):
            _, err = self.run_cli(self.args("--apply"), expected=1)
        self.assertIn("failed stage: read-back", err)
        self.assertIn("invalid local data or API response", err)
        self.assertNotIn("private details", err)
        self.assertNotIn("Traceback", err)

    def test_post_credential_validation_failure_has_accurate_recovery(self):
        validate = golive.validate_wiring
        calls = 0

        def fail_second(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise golive.GoLiveError("Wiring validation failed")
            return validate(*args)

        with patch.object(golive, "validate_wiring", side_effect=fail_second):
            _, err = self.run_cli(self.args("--apply"), expected=1)
        self.assertIn("failed stage: wiring validation", err)
        self.assertIn("credential: created", err)
        self.assertIn("No workflow rollback needed", err)
        self.assertNotIn("--rollback", err)
        self.assertEqual([c[0] for c in self.writes()], ["POST"])

    def test_old_backup_format_is_refused_before_any_api_access(self):
        self.run_cli(self.args())
        (path,) = self.backups.glob("*.json")
        saved = json.loads(path.read_text())
        for version in (1, None):
            with self.subTest(version=version):
                saved["format_version"] = version
                path.write_text(json.dumps(saved))
                self.fake.calls.clear()
                _, err = self.run_cli(["--rollback", str(path)], expected=1)
                self.assertIn("require format_version 2; old backups are refused", err)
                self.assertEqual(self.fake.calls, [])


if __name__ == "__main__":
    unittest.main()
