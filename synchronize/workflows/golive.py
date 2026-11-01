"""Back up, merge template deltas, wire and publish Dograh reception workflows.

Successor to the proof workspace's deploy_live.py: the same exact-once line/word
merge, with local git refs, explicit targets, credential wiring and rollback.
Standard library only. Nothing contacts Railway or fetches git refs.
"""

import argparse
import copy
from datetime import datetime, timezone
import difflib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from uuid import UUID, uuid4


ROOT = Path(__file__).resolve().parents[2]
TARGETS = {3: "en", 1: "ko"}  # 2 is structurally different Korean realtime.
START_FIELDS = {
    "pre_call_fetch_mode",
    "pre_call_fetch_url",
    "pre_call_fetch_credential_uuid",
}
SETTINGS = ("workflow_configurations", "template_context_variables")
PENDING_UUID = "00000000-0000-4000-8000-000000000000"
PROBE_RUN_ID = -2147483647  # Receiver ignores run ids; cannot identify a real call.
MISSING = object()


class GoLiveError(Exception):
    """An operator-safe message: never include HTTP bodies or secret values."""


class UncertainWrite(GoLiveError):
    """A transport/response failure cannot establish whether a write committed."""


def require(condition, message):
    if not condition:
        raise GoLiveError(message)


def env_value(name, required=True):
    value = os.environ.get(name, "")
    require(not required or bool(value), f"Set {name}")
    require(
        not any(c in "\"'" or ord(c) < 32 or ord(c) == 127 for c in value),
        f"Invalid {name}",
    )
    return value


def origin(value, name="--runtime-origin"):
    """HTTPS origins only, with HTTP loopback allowed for offline fake servers."""
    try:
        parts = urllib.parse.urlsplit(value)
        host = (parts.hostname or "").lower()
        loopback = host == "localhost"
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        valid = parts.scheme == "https" or (parts.scheme == "http" and loopback)
        valid = valid and bool(host) and parts.port != 0
        valid = valid and not (
            parts.username or parts.password or parts.query or parts.fragment
        )
        valid = valid and parts.path in ("", "/")
        valid = valid and not any(c.isspace() or c in "<>\"'\\" for c in value)
        valid = valid and not any(
            token in host for token in (".invalid", "placeholder", "your-", "your_")
        )
        valid = valid and not any(
            host == reserved or host.endswith("." + reserved)
            for reserved in ("example.com", "example.org", "example.net")
        )
        try:
            ipaddress.ip_address(host)
        except ValueError:
            valid = valid and (
                loopback
                or (
                    "." in host
                    and all(
                        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                        for label in host.split(".")
                    )
                )
            )
        require(
            valid,
            f"{name}: expected a real HTTPS origin (HTTP loopback is allowed for offline tests)",
        )
    except ValueError:
        raise GoLiveError(f"{name}: invalid origin") from None
    return value.rstrip("/")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Do not forward either secret to another URL.


def request(method, url, headers, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **headers},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(req, timeout=30) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        return status, None  # Errors may echo submitted secrets; discard them.
    except (OSError, ValueError, http.client.HTTPException):
        raise UncertainWrite(
            "HTTP transport failed; unknown, may have committed (details suppressed)"
        ) from None
    try:
        return status, json.loads(raw) if raw else None
    except (ValueError, UnicodeError):
        raise UncertainWrite(
            "HTTP response was not JSON; unknown, may have committed"
        ) from None


class Dograh:
    def __init__(self):
        self.base = origin(env_value("DOGRAH_PROOF_BASE_URL"), "DOGRAH_PROOF_BASE_URL")
        self.key = env_value("DOGRAH_PROOF_API_KEY")

    def api(self, method, path, body=None):
        status, result = request(
            method, self.base + "/api/v1" + path, {"X-API-Key": self.key}, body
        )
        if status >= 500:
            raise UncertainWrite(
                f"Dograh {method} failed: HTTP {status}; unknown, may have committed"
            )
        require(200 <= status < 300, f"Dograh {method} failed: HTTP {status}")
        expected = list if method == "GET" and path == "/credentials/" else dict
        if not isinstance(result, expected):
            error = GoLiveError if method == "GET" else UncertainWrite
            raise error(
                f"Dograh {method} returned a malformed response (expected JSON {expected.__name__})"
                + ("; unknown, may have committed" if method != "GET" else "")
            )
        return result


def template(ref, lang):
    resolved = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{ref}^{{commit}}",
        ],
        capture_output=True,
        text=True,
    )
    require(resolved.returncode == 0, "Cannot resolve local git commit ref")
    result = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "show",
            f"{resolved.stdout.strip()}:synchronize/workflows/consultation-{lang}.json",
        ],
        capture_output=True,
        text=True,
    )
    require(result.returncode == 0, "Cannot read template from local git ref")
    return json.loads(result.stdout)["workflow_definition"]


def conflict(label):
    return GoLiveError(
        f"{label}: replacement must match exactly once; edit that field in the "
        "Dograh UI, publish it, then re-plan and apply. Check LF/CRLF and NFC/NFD "
        "Unicode differences if the text looks identical."
    )


def replace_once(live, old, new, label):
    if not old or live.count(old) != 1:
        raise conflict(label)
    return live.replace(old, new, 1)


def character_merge(old, new, live, label):
    # Only a single old line expanding into multiple lines may use this path.
    # Both literal sides must survive, as whole words, through the field end.
    require("\n" not in old and "\n" in new, str(conflict(label)))
    prefix = 0
    while prefix < min(len(old), len(new)) and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < min(len(old), len(new)) - prefix
        and old[-suffix - 1] == new[-suffix - 1]
    ):
        suffix += 1
    old_middle = old[prefix : len(old) - suffix if suffix else len(old)]
    new_middle = new[prefix : len(new) - suffix if suffix else len(new)]
    right = old[len(old) - suffix :] if suffix else ""
    left_literal = old[:prefix].rsplit("}}", 1)[-1]

    def boundary(index):
        return index in (0, len(old)) or not (
            old[index - 1].isalnum() and old[index].isalnum()
        )

    if not (
        len(left_literal.strip()) >= 8
        and len(right.strip()) >= 8
        and any(c.isalnum() for c in left_literal)
        and any(c.isalnum() for c in right)
        and boundary(prefix - len(left_literal))
        and boundary(prefix)
        and boundary(len(old) - suffix)
    ):
        raise conflict(label)
    before = left_literal + old_middle + right
    after = left_literal + new_middle + right

    def anchored(text):
        if live.count(text) != 1 or not live.endswith(text):
            return False
        start = len(live) - len(text)
        return not start or not (live[start - 1].isalnum() and text[0].isalnum())

    if anchored(after) and before not in live:
        return live
    if "\n" in live or not anchored(before):
        raise conflict(label)
    return replace_once(live, before, after, label)


def line_matches(lines, block):
    return [
        i
        for i in range(len(lines) - len(block) + 1)
        if block and lines[i : i + len(block)] == block
    ]


def patch_text(old, new, live, label):
    """Exact-once hunks; already-applied evidence includes unchanged context."""
    require(isinstance(live, str), f"{label}: expected text")
    if live == old or live == new:
        return new
    # Whole new-template context may have retained local lines around it.
    if (
        "\n" in new
        and len(line_matches(live.split("\n"), new.split("\n"))) == 1
        and old not in live
    ):
        return live
    if "\n" not in old:
        return character_merge(old, new, live, label)
    ol, nl = old.split("\n"), new.split("\n")
    out = live
    opcodes = difflib.SequenceMatcher(None, ol, nl, autojunk=False).get_opcodes()
    for index, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        if tag == "equal":
            continue
        before, after = ol[i1:i2], nl[j1:j2]
        # Use up to two adjacent unchanged lines on EACH side. Never borrow
        # another hunk as context (it may already have been applied).
        left = opcodes[index - 1] if index else None
        right = opcodes[index + 1] if index + 1 < len(opcodes) else None
        pre = ol[max(left[1], i1 - 2) : i1] if left and left[0] == "equal" else []
        post = ol[i2 : min(right[2], i2 + 2)] if right and right[0] == "equal" else []
        lines = out.split("\n")
        anchored_before, anchored_after = pre + before + post, pre + after + post
        # A new fragment elsewhere, or a surviving delete anchor alone, does
        # not prove a hunk applied. Absence means the entire old block is absent.
        if (
            not line_matches(lines, before if before else anchored_before)
            and (pre or post)
            and len(line_matches(lines, anchored_after)) == 1
        ):
            continue
        search = anchored_before if tag in ("insert", "delete") else before
        replacement = anchored_after if tag in ("insert", "delete") else after
        found = line_matches(lines, search)
        if len(found) == 1:
            start = found[0]
            out = "\n".join(lines[:start] + replacement + lines[start + len(search) :])
        else:
            raise conflict(label)
    return out


def patch_value(old, new, live, label):
    if live == old or live == new:
        return new if new is MISSING else copy.deepcopy(new)
    if all(isinstance(v, str) for v in (old, new, live)):
        return patch_text(old, new, live, label)
    if all(isinstance(v, dict) for v in (old, new, live)):
        out = copy.deepcopy(live)
        for key in sorted(old.keys() | new.keys()):
            ov, nv = old.get(key, MISSING), new.get(key, MISSING)
            if ov != nv:
                value = patch_value(ov, nv, out.get(key, MISSING), label + "." + key)
                if value is MISSING:
                    out.pop(key, None)
                else:
                    out[key] = value
        return out
    raise GoLiveError(
        f"{label}: value differs from both templates; refusing to overwrite customisation"
    )


def nodes(definition):
    result = {node["id"]: node for node in definition["nodes"]}
    require(len(result) == len(definition["nodes"]), "Duplicate node ids")
    return result


def patched(live, old, new, runtime, credential_uuid, field_modes=None):
    out = copy.deepcopy(live)
    before, previous, current = nodes(out), nodes(old), nodes(new)
    for node_id in sorted(previous.keys() | current.keys()):
        if node_id == "6":
            continue
        require(
            node_id in previous and node_id in current and node_id in before,
            f"node {node_id}: template/live structure differs",
        )
        a, b, target = previous[node_id], current[node_id], before[node_id]
        require(
            a["type"] == b["type"] == target["type"], f"node {node_id}: type differs"
        )
        for field in sorted(a["data"].keys() | b["data"].keys()):
            if node_id == "1" and field in START_FIELDS:
                continue  # Wiring always uses operator values, never template placeholders.
            ov, nv = a["data"].get(field, MISSING), b["data"].get(field, MISSING)
            if ov == nv:
                continue
            label = f"node {node_id}.data.{field}"
            live_value = target["data"].get(field, MISSING)
            value = patch_value(
                ov,
                nv,
                live_value,
                label,
            )
            if field_modes is not None and value != live_value:
                field_modes[label] = (
                    "template-replaced" if live_value == ov else "MERGED-INTO-CUSTOM"
                )
            if value is MISSING:
                target["data"].pop(field, None)
            else:
                target["data"][field] = value
    require(before["1"]["type"] == "startCall", "node 1: expected Start Call")
    require(
        current["1"]["data"]["pre_call_fetch_mode"] == "inbound",
        "Template must use inbound pre-call fetch",
    )
    before["1"]["data"].update(
        pre_call_fetch_mode=current["1"]["data"]["pre_call_fetch_mode"],
        pre_call_fetch_url=runtime + "/api/voice/pre-call",
        pre_call_fetch_credential_uuid=credential_uuid,
    )
    if "6" not in before:
        require("6" in current, "Template missing webhook node 6")
        out["nodes"].append(copy.deepcopy(current["6"]))
    webhook = nodes(out)["6"]
    require(webhook["type"] == "webhook", "node 6: expected webhook")
    require(
        webhook["data"].get("enabled", True) is True
        and webhook["data"].get("http_method") == "POST",
        "node 6: existing webhook must be enabled POST",
    )
    require(
        not any(e.get("source") == "6" or e.get("target") == "6" for e in out["edges"]),
        "node 6: webhook must have no edges",
    )
    webhook["data"].update(
        endpoint_url=runtime + "/api/voice/call-ended", credential_uuid=credential_uuid
    )
    validate_wiring(out, live, runtime, credential_uuid)
    return out


def validate_wiring(definition, original, runtime, credential_uuid):
    indexed = nodes(definition)
    require(nodes(original).keys() <= indexed.keys(), "A pre-existing node was lost")
    expected = {
        "pre_call_fetch_mode": "inbound",
        "pre_call_fetch_url": runtime + "/api/voice/pre-call",
        "pre_call_fetch_credential_uuid": credential_uuid,
    }
    require(
        all(indexed["1"]["data"].get(k) == v for k, v in expected.items()),
        "Start wiring verification failed",
    )
    require(
        indexed["6"]["data"].get("endpoint_url") == runtime + "/api/voice/call-ended"
        and indexed["6"]["data"].get("credential_uuid") == credential_uuid,
        "Webhook wiring verification failed",
    )
    text = json.dumps(definition).lower()
    require(
        ".invalid" not in text and "<dograh" not in text,
        "Unresolved placeholder in workflow",
    )


def backup_directory(path):
    path = path.expanduser().resolve()
    require(
        not any(
            path == root or root in path.parents
            for root in (Path("/tmp"), Path("/private/tmp"))
        ),
        "Backup directory must be durable; use ~/golive-backups, not /tmp or /private/tmp",
    )
    ancestor = path
    while not ancestor.exists():
        ancestor = ancestor.parent
    result = subprocess.run(
        ["git", "-C", str(ancestor), "rev-parse", "--git-dir"], capture_output=True
    )
    require(result.returncode != 0, "Backup directory must be outside git")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def ensure_no_secrets(value):
    def contains(item, secret):
        if isinstance(item, str):
            return secret in item
        if isinstance(item, dict):
            return any(
                contains(key, secret) or contains(nested, secret)
                for key, nested in item.items()
            )
        if isinstance(item, list):
            return any(contains(nested, secret) for nested in item)
        return False

    for name in ("DOGRAH_WEBHOOK_SECRET", "DOGRAH_PROOF_API_KEY"):
        secret = os.environ.get(name)
        require(
            not secret or not contains(value, secret),
            "Refusing to persist or display a secret in workflow data",
        )


def require_published(workflow, workflow_id):
    require(
        workflow.get("version_status") == "published",
        f"an unpublished draft exists on workflow {workflow_id}; "
        "publish or discard it in the Dograh UI first",
    )


def same_snapshot(workflow, snapshot):
    return all(
        workflow.get(k) == snapshot.get(k)
        for k in (
            "workflow_definition",
            *SETTINGS,
            "version_status",
            "version_number",
            "current_definition_id",
        )
    )


def backup(client, workflow_id, directory, *, allow_draft=False):
    workflow = client.api("GET", f"/workflow/fetch/{workflow_id}")
    require(workflow["id"] == workflow_id, "Fetched workflow id mismatch")
    if not allow_draft:
        require_published(workflow, workflow_id)
    snapshot = {
        "format_version": 2,
        "workflow_id": workflow_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "workflow_definition": workflow["workflow_definition"],
        **{
            field: workflow[field]
            for field in ("version_status", "version_number", "current_definition_id")
        },
        **{field: workflow[field] for field in SETTINGS},
    }
    ensure_no_secrets(snapshot)
    path = (
        directory
        / f"workflow-{workflow_id}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:8]}.json"
    )
    ensure_no_secrets(str(path))
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    print(f"Backup: {path}")
    print(
        f"Workflow {workflow_id}: {snapshot['version_status']} version "
        f"{snapshot['version_number']}; published definition id {snapshot['current_definition_id']}"
    )
    return snapshot, path


def find_credential(client, name):
    entries = client.api("GET", "/credentials/")
    matches = [entry for entry in entries if entry["name"] == name]
    require(len(matches) <= 1, "Credential name is ambiguous")
    if matches:
        require(
            matches[0]["credential_type"] == "custom_header",
            "Existing credential must be custom_header",
        )
        return str(UUID(matches[0]["uuid"]))
    return None


def credential(client, name, secret, ident, state):
    body = {
        "credential_type": "custom_header",
        "credential_data": {"header_name": "X-Dograh-Secret", "header_value": secret},
    }
    if ident:
        result = state.write(
            "credential rotate",
            "rotated",
            lambda: client.api("PUT", f"/credentials/{ident}", body),
        )
    else:
        result = state.write(
            "credential create",
            "created",
            lambda: client.api("POST", "/credentials/", {"name": name, **body}),
        )
    return str(UUID(result["uuid"]))


def diff_fields(before, after):
    """Readable field diff without emitting full prompts or HTTP response data."""
    changes = []
    previous = nodes(before)
    for node_id in previous.keys() - nodes(after).keys():
        changes.append((node_id, "[removed node]", previous[node_id]["type"], None))
    for node_id, node in nodes(after).items():
        if node_id not in previous:
            changes.append((node_id, "[added node]", None, node["type"]))
            if node_id == "6":
                for field in ("endpoint_url", "credential_uuid"):
                    changes.append((node_id, field, None, node["data"][field]))
            continue
        a, b = previous[node_id]["data"], node["data"]
        for field in sorted(a.keys() | b.keys()):
            if a.get(field) != b.get(field) or (field in a) != (field in b):
                changes.append((node_id, field, a.get(field), b.get(field)))
    return changes


def show_plan(changes, field_modes=None):
    for node_id, field, old, new in changes:
        label = f"node {node_id}.data.{field}"
        mode = (
            field_modes.get(label, "wiring")
            if field_modes is not None else "restore-backup"
        )
        old_lines = (
            old.splitlines() if isinstance(old, str)
            else json.dumps(old, indent=2).splitlines()
        )
        new_lines = (
            new.splitlines() if isinstance(new, str)
            else json.dumps(new, indent=2).splitlines()
        )
        removed = added = 0
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, old_lines, new_lines, autojunk=False
        ).get_opcodes():
            if tag != "equal":
                removed += i2 - i1
                added += j2 - j1
        print(f"{label}: {mode}; -{removed}/+{added} changed lines")
        if field in {"pre_call_fetch_url", "endpoint_url"}:
            path = (
                "/api/voice/pre-call"
                if field == "pre_call_fetch_url"
                else "/api/voice/call-ended"
            )
            print(
                f"  - {'configured URL' if old else 'unset'}\n  + runtime origin + {path}"
            )
        elif field in START_FIELDS | {
            "credential_uuid",
            "[added node]",
            "[removed node]",
        }:
            print(f"  - {json.dumps(old)}\n  + {json.dumps(new)}")
        elif isinstance(old, str) and isinstance(new, str):
            if mode == "MERGED-INTO-CUSTOM":
                # Show changed lines only, so an operator can review the merge
                # before explicitly accepting this field. Backup/plan secret
                # checks have already passed; never emit unchanged prompts.
                for line in list(
                    difflib.unified_diff(old_lines, new_lines, n=0, lineterm="")
                )[2:]:
                    print("  " + line)
            else:
                print("  (prompt content withheld)")
        else:
            print(
                f"  - {type(old).__name__}\n  + {type(new).__name__} (value withheld)"
            )


def verify_snapshot(workflow, expected_definition, snapshot):
    require(
        workflow["workflow_definition"] == expected_definition,
        "Published definition differs from planned definition",
    )
    for field in SETTINGS:
        require(workflow[field] == snapshot[field], f"{field}: changed during publish")
    require(workflow.get("version_status") == "published", "Workflow is not published")


def summary(status, workflow_id, changes, existing_count):
    print(
        f"{status} workflow {workflow_id}: {len(changes)} field/node changes; {existing_count} existing nodes preserved"
    )
    grouped = {}
    for node_id, field, _, _ in changes:
        grouped.setdefault(node_id, []).append(field)
    for node_id, fields in grouped.items():
        print(f"  node {node_id}: {', '.join(fields)}")


class WriteState:
    """Keep recovery evidence even when the request's outcome is uncertain."""

    def __init__(self, path, draft="no"):
        self.path, self.stage = path, "pre-write"
        self.credential, self.published, self.draft = "unchanged", "unchanged", draft
        self.attempted = False

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        if error is not None and self.attempted:
            print(
                f"RECOVERY — failed stage: {self.stage}\n"
                f"  credential: {self.credential}\n"
                f"  live published definition: {self.published}\n"
                f"  draft left behind: {self.draft}",
                file=sys.stderr,
            )
            if self.published == "unchanged" and self.draft == "no":
                print(
                    "  No workflow rollback needed: published definition unchanged; no draft saved. "
                    "Any credential change is retained.",
                    file=sys.stderr,
                )
            else:
                print(
                    f"  python3 {shlex.quote(str(Path(__file__).resolve()))} --rollback {shlex.quote(str(self.path))}",
                    file=sys.stderr,
                )
            if self.draft != "no":
                print(
                    "  Anyone pressing Publish in the Dograh UI may ship the wiring; "
                    "the draft must be rolled back or discarded first.",
                    file=sys.stderr,
                )

    def write(self, stage, outcome, action):
        self.stage, self.attempted = stage, True
        print(f"Stage: {stage}")
        try:
            result = action()
        except Exception as error:
            if isinstance(error, UncertainWrite) or not isinstance(error, GoLiveError):
                if stage.startswith("credential"):
                    self.credential = "unknown, may have been created/rotated"
                elif stage == "draft save":
                    self.draft = "unknown, may have committed"
                elif stage == "publish":
                    self.published = "unknown, may have committed"
                    self.draft = "unknown"
            raise
        if stage.startswith("credential"):
            self.credential = outcome
        elif stage == "draft save":
            self.draft = "yes"
        elif stage == "publish":
            self.published, self.draft = "changed", "no"
        return result


def publish(client, workflow_id, body, state):
    state.write(
        "draft save", None, lambda: client.api("PUT", f"/workflow/{workflow_id}", body)
    )
    state.write(
        "publish",
        None,
        lambda: client.api("POST", f"/workflow/{workflow_id}/publish", {}),
    )
    state.stage = "read-back"
    print("Stage: read-back")
    fetched = client.api("GET", f"/workflow/fetch/{workflow_id}")
    state.draft = {"draft": "yes", "published": "no"}.get(
        fetched.get("version_status"), "unknown"
    )
    return fetched


def deploy(args):
    require(
        TARGETS.get(args.workflow_id) == args.lang,
        "Use workflow 3/en or 1/ko; workflow 2 is excluded",
    )
    runtime = origin(args.runtime_origin)
    secret = env_value("DOGRAH_WEBHOOK_SECRET", required=args.apply)
    if args.apply:
        print(
            "Before apply: deploy the runtime with this secret active and run golive probe first."
        )
    old, new = template(args.old_ref, args.lang), template(args.new_ref, args.lang)
    client = Dograh()
    snapshot, path = backup(client, args.workflow_id, backup_directory(args.backup_dir))
    live = snapshot["workflow_definition"]
    existing = find_credential(client, args.credential_name)
    field_modes = {}
    planned = patched(live, old, new, runtime, existing or PENDING_UUID, field_modes)
    ensure_no_secrets(planned)
    changes = diff_fields(live, planned)
    show_plan(changes, field_modes)
    merged = {
        field for field, mode in field_modes.items() if mode == "MERGED-INTO-CUSTOM"
    }
    new_nodes = nodes(new)
    known_fields = {
        f"node {node_id}.data.{field}"
        for node_id, node in nodes(old).items()
        if node_id in new_nodes and node_id != "6"
        for field in node["data"].keys() | new_nodes[node_id]["data"].keys()
        if node["data"].get(field, MISSING)
        != new_nodes[node_id]["data"].get(field, MISSING)
        and not (node_id == "1" and field in START_FIELDS)
    }
    require(
        set(args.accept_merged) <= known_fields,
        "--accept-merged must name a changed template field exactly as printed",
    )
    already_wired = planned == live and existing is not None
    print(
        "Already wired; no remote writes"
        if already_wired
        else "Would create/update credential (secret not shown)"
    )
    if not args.apply:
        summary("PLAN", args.workflow_id, changes, len(nodes(live)))
        print("No remote writes")
        return
    unaccepted = merged - set(args.accept_merged)
    require(
        not unaccepted,
        "Review the MERGED-INTO-CUSTOM diff, then explicitly accept each field: "
        + " ".join(f"--accept-merged {shlex.quote(field)}" for field in sorted(unaccepted)),
    )
    # Fresh backup and all exact-once patches must succeed before ANY write,
    # including credential rotation. Also detect concurrent workflow edits.
    fresh = client.api("GET", f"/workflow/fetch/{args.workflow_id}")
    require_published(fresh, args.workflow_id)
    require(same_snapshot(fresh, snapshot), "Workflow changed since backup; rerun plan")
    require(path.is_file(), "Fresh backup missing")
    print(f"ROLLBACK FILE: {path}")
    if already_wired:
        return
    with WriteState(path) as state:
        ident = credential(client, args.credential_name, secret, existing, state)
        # Only substitute the UUID: do not re-run the merge after a remote write.
        planned_nodes = nodes(planned)
        planned_nodes["1"]["data"]["pre_call_fetch_credential_uuid"] = ident
        planned_nodes["6"]["data"]["credential_uuid"] = ident
        state.stage = "wiring validation"
        print("Stage: wiring validation")
        validate_wiring(planned, live, runtime, ident)
        fetched = publish(
            client, args.workflow_id, {"workflow_definition": planned}, state
        )
        verify_snapshot(fetched, planned, snapshot)
        validate_wiring(fetched["workflow_definition"], live, runtime, ident)
    summary("APPLIED", args.workflow_id, changes, len(nodes(live)))
    print("Published and verified; settings/model override unchanged")


def rollback(args):
    snapshot = json.loads(args.rollback.read_text(encoding="utf-8"))
    require(
        snapshot.get("format_version") == 2,
        "Unsupported backup format: require format_version 2; old backups are refused",
    )
    require(
        snapshot.get("version_status") == "published",
        "Cannot restore a draft backup or a backup without published-version evidence",
    )
    workflow_id = snapshot["workflow_id"]
    require(workflow_id in TARGETS, "Backup target must be workflow 1 or 3")
    if args.workflow_id is not None:
        require(args.workflow_id == workflow_id, "Rollback target differs from backup")
    ensure_no_secrets(snapshot)
    directory = backup_directory(args.backup_dir or args.rollback.resolve().parent)
    client = Dograh()
    # Recovery must work when a failed apply left a draft. Preserve it for
    # inspection, clearly labelled; never allow that draft backup as a restore target.
    current, _ = backup(client, workflow_id, directory, allow_draft=True)
    print("Rollback overwrites later edits, including any unpublished draft.")
    show_plan(
        diff_fields(current["workflow_definition"], snapshot["workflow_definition"])
    )
    body = {
        "workflow_definition": snapshot["workflow_definition"],
        **{k: snapshot[k] for k in SETTINGS if current[k] != snapshot[k]},
    }
    for field in SETTINGS:
        if field in body:
            print(f"{field}: restore backup value (content withheld)")
    fresh = client.api("GET", f"/workflow/fetch/{workflow_id}")
    require(
        same_snapshot(fresh, current), "Workflow changed since backup; rerun rollback"
    )
    with WriteState(
        args.rollback.resolve(),
        draft="yes" if current["version_status"] == "draft" else "no",
    ) as state:
        fetched = publish(client, workflow_id, body, state)
        verify_snapshot(fetched, snapshot["workflow_definition"], snapshot)
    print(
        f"ROLLED BACK workflow {workflow_id}: definition and settings equal backup; credential retained"
    )


def probe(args):
    runtime, secret = origin(args.runtime_origin), env_value("DOGRAH_WEBHOOK_SECRET")
    require(
        re.fullmatch(r"\+[1-9]\d{7,14}", args.to_number) is not None,
        "Use an E.164 to-number",
    )
    body = {
        "event": "call_inbound",
        "call_inbound": {
            "agent_id": args.workflow_id,
            "from_number": "+12025550100",
            "to_number": args.to_number,
            "workflow_run_id": PROBE_RUN_ID,
        },
    }
    status, data = request(
        "POST",
        runtime + "/api/voice/pre-call",
        {
            "X-Dograh-Secret": secret,
            "Idempotency-Key": f"dograh-pre-call-{PROBE_RUN_ID}",
        },
        body,
    )
    print(status)
    require(
        status == 200 and isinstance(data, dict),
        "Authenticated probe expected HTTP 200 JSON object",
    )
    wrong = "wrong-" + uuid4().hex
    while wrong == secret:
        wrong = "wrong-" + uuid4().hex
    status, _ = request(
        "POST",
        runtime + "/api/voice/pre-call",
        {
            "X-Dograh-Secret": wrong,
            "Idempotency-Key": f"dograh-pre-call-{PROBE_RUN_ID}",
        },
        body,
    )
    print(status)
    require(status in (401, 403), "Wrong-secret probe expected HTTP 401/403")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("plan", "probe"), default="plan")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--accept-merged", action="append", default=[], metavar="FIELD")
    parser.add_argument("--rollback", type=Path)
    parser.add_argument("--workflow-id", type=int)
    parser.add_argument("--lang", choices=("en", "ko"))
    parser.add_argument("--old-ref", default="940fdef")
    parser.add_argument("--new-ref", default="HEAD")
    parser.add_argument("--runtime-origin")
    parser.add_argument("--credential-name")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--to-number")
    args = parser.parse_args(argv)
    try:
        require(not (args.apply and args.rollback), "Choose apply or rollback")
        require(
            not args.accept_merged or (args.command == "plan" and not args.rollback),
            "--accept-merged is only for plan/apply",
        )
        if args.command == "probe":
            require(
                not args.apply and not args.rollback,
                "Probe cannot apply or roll back workflows",
            )
            require(
                args.runtime_origin and args.to_number and args.workflow_id in TARGETS,
                "Probe needs --runtime-origin, --to-number and --workflow-id (1 or 3)",
            )
            probe(args)
        elif args.rollback:
            rollback(args)
        else:
            require(
                all(
                    (
                        args.workflow_id,
                        args.lang,
                        args.runtime_origin,
                        args.credential_name,
                        args.backup_dir,
                    )
                ),
                "Plan/apply needs --workflow-id, --lang, --runtime-origin, --credential-name, --backup-dir",
            )
            deploy(args)
    except GoLiveError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print(
            "Error: invalid local data or API response (details suppressed)",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
