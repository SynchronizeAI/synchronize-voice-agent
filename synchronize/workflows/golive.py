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
MISSING = object()


class GoLiveError(Exception):
    """An operator-safe message: never include HTTP bodies or secret values."""


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


def origin(value):
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
            token in host
            for token in (".invalid", "example", "placeholder", "your-", "your_")
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
            "Expected a real HTTPS origin (HTTP loopback is allowed for offline tests)",
        )
    except ValueError:
        raise GoLiveError("Invalid origin") from None
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
        raise GoLiveError("HTTP request failed (details suppressed)") from None
    try:
        return status, json.loads(raw) if raw else None
    except (ValueError, UnicodeError):
        raise GoLiveError("HTTP response was not JSON") from None


class Dograh:
    def __init__(self):
        self.base = origin(env_value("DOGRAH_PROOF_BASE_URL"))
        self.key = env_value("DOGRAH_PROOF_API_KEY")

    def api(self, method, path, body=None):
        status, result = request(
            method, self.base + "/api/v1" + path, {"X-API-Key": self.key}, body
        )
        require(200 <= status < 300, f"Dograh {method} failed: HTTP {status}")
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


def replace_once(live, old, new, label):
    count = live.count(old) if old else 0
    # A repeated run may already contain the exact new block, plus custom text.
    if new and live.count(new) == 1 and (count == 0 or (count == 1 and old in new)):
        return live
    require(count == 1, f"{label}: replacement must match exactly once")
    return live.replace(old, new, 1)


def replace_line_block(live, old, new, label):
    # Whole lines prevent an anchor such as A also matching inside LOCAL.
    lines, before, after = live.split("\n"), old.split("\n"), new.split("\n")

    def matches(haystack, needle):
        return [
            i
            for i in range(len(haystack) - len(needle) + 1)
            if haystack[i : i + len(needle)] == needle
        ]

    found = matches(lines, before)
    if len(matches(lines, after)) == 1 and (
        not found or (len(found) == 1 and matches(after, before))
    ):
        return live
    require(len(found) == 1, f"{label}: replacement must match exactly once")
    start = found[0]
    return "\n".join(lines[:start] + after + lines[start + len(before) :])


def patch_text(old, new, live, label):
    """Preserve deploy_live's exact-once merge; anchor added/deleted line blocks."""
    require(isinstance(live, str), f"{label}: expected text")
    if live == old or live == new:
        return new
    if "\n" in old or "\n" in new:
        ol, nl = old.split("\n"), new.split("\n")
        out = live
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, ol, nl, autojunk=False
        ).get_opcodes():
            if tag == "equal":
                continue
            ob, nb = "\n".join(ol[i1:i2]), "\n".join(nl[j1:j2])
            if tag == "insert":
                if i1 < len(ol):
                    ob, nb = ol[i1], nb + "\n" + ol[i1]
                else:
                    ob, nb = ol[-1], ol[-1] + "\n" + nb
            elif tag == "delete":
                if i2 < len(ol):
                    ob, nb = ob + "\n" + ol[i2], ol[i2]
                else:
                    ob, nb = ol[i1 - 1] + "\n" + ob, ol[i1 - 1]
            out = replace_line_block(out, ob, nb, label)
        return out
    p = 0
    while p < min(len(old), len(new)) and old[p] == new[p]:
        p += 1
    # Original one-line merge: differing middle anchored by unchanged suffix.
    return replace_once(live, old[p:], new[p:], label)


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


def patched(live, old, new, runtime, credential_uuid):
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
            value = patch_value(
                ov,
                nv,
                target["data"].get(field, MISSING),
                f"node {node_id}.data.{field}",
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
        webhook["data"].get("enabled") is True
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


def backup(client, workflow_id, directory):
    workflow = client.api("GET", f"/workflow/fetch/{workflow_id}")
    require(workflow["id"] == workflow_id, "Fetched workflow id mismatch")
    snapshot = {
        "format_version": 1,
        "workflow_id": workflow_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "workflow_definition": workflow["workflow_definition"],
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
    return snapshot, path


def credential(client, name, secret):
    entries = client.api("GET", "/credentials/")
    matches = [entry for entry in entries if entry["name"] == name]
    require(len(matches) <= 1, "Credential name is ambiguous")
    body = {
        "credential_type": "custom_header",
        "credential_data": {"header_name": "X-Dograh-Secret", "header_value": secret},
    }
    if matches:
        require(
            matches[0]["credential_type"] == "custom_header",
            "Existing credential must be custom_header",
        )
        ident = str(UUID(matches[0]["uuid"]))
        result = client.api("PUT", f"/credentials/{ident}", body)
    else:
        result = client.api("POST", "/credentials/", {"name": name, **body})
    return str(UUID(result["uuid"]))


def diff_fields(before, after):
    """Readable field diff without emitting full prompts or HTTP response data."""
    changes = []
    previous = nodes(before)
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


def show_plan(changes):
    for node_id, field, old, new in changes:
        print(f"node {node_id}.data.{field}")
        if field in {"pre_call_fetch_url", "endpoint_url"}:
            path = (
                "/api/voice/pre-call"
                if field == "pre_call_fetch_url"
                else "/api/voice/call-ended"
            )
            print(
                f"  - {'configured URL' if old else 'unset'}\n  + runtime origin + {path}"
            )
        elif field in START_FIELDS | {"credential_uuid", "[added node]"}:
            print(f"  - {json.dumps(old)}\n  + {json.dumps(new)}")
        elif isinstance(old, str) and isinstance(new, str):
            removed = added = 0
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                None, old.splitlines(), new.splitlines(), autojunk=False
            ).get_opcodes():
                if tag != "equal":
                    removed += i2 - i1
                    added += j2 - j1
            print(
                f"  - {removed} changed lines\n  + {added} changed lines (prompt content withheld)"
            )
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


def publish(client, workflow_id, body):
    client.api("PUT", f"/workflow/{workflow_id}", body)
    client.api("POST", f"/workflow/{workflow_id}/publish", {})
    return client.api("GET", f"/workflow/fetch/{workflow_id}")


def deploy(args):
    require(
        TARGETS.get(args.workflow_id) == args.lang,
        "Use workflow 3/en or 1/ko; workflow 2 is excluded",
    )
    runtime = origin(args.runtime_origin)
    secret = env_value("DOGRAH_WEBHOOK_SECRET", required=args.apply)
    old, new = template(args.old_ref, args.lang), template(args.new_ref, args.lang)
    client = Dograh()
    snapshot, path = backup(client, args.workflow_id, backup_directory(args.backup_dir))
    live = snapshot["workflow_definition"]
    planned = patched(live, old, new, runtime, PENDING_UUID)
    ensure_no_secrets(planned)
    changes = diff_fields(live, planned)
    show_plan(changes)
    print("Would create/update credential (secret not shown)")
    if not args.apply:
        summary("PLAN", args.workflow_id, changes, len(nodes(live)))
        print("No remote writes")
        return
    # Fresh backup and all exact-once patches must succeed before ANY write,
    # including credential rotation. Also detect concurrent workflow edits.
    fresh = client.api("GET", f"/workflow/fetch/{args.workflow_id}")
    require(
        fresh["workflow_definition"] == live
        and all(fresh[k] == snapshot[k] for k in SETTINGS),
        "Workflow changed since backup; rerun plan",
    )
    require(path.is_file(), "Fresh backup missing")
    ident = credential(client, args.credential_name, secret)
    # Only substitute the UUID: do not re-run the merge after a remote write.
    planned_nodes = nodes(planned)
    planned_nodes["1"]["data"]["pre_call_fetch_credential_uuid"] = ident
    planned_nodes["6"]["data"]["credential_uuid"] = ident
    validate_wiring(planned, live, runtime, ident)
    fetched = publish(client, args.workflow_id, {"workflow_definition": planned})
    verify_snapshot(fetched, planned, snapshot)
    validate_wiring(fetched["workflow_definition"], live, runtime, ident)
    summary("APPLIED", args.workflow_id, changes, len(nodes(live)))
    print("Published and verified; settings/model override unchanged")


def rollback(args):
    snapshot = json.loads(args.rollback.read_text(encoding="utf-8"))
    require(snapshot.get("format_version") == 1, "Unsupported backup format")
    workflow_id = snapshot["workflow_id"]
    require(workflow_id in TARGETS, "Backup target must be workflow 1 or 3")
    if args.workflow_id is not None:
        require(args.workflow_id == workflow_id, "Rollback target differs from backup")
    ensure_no_secrets(snapshot)
    directory = backup_directory(args.backup_dir or args.rollback.resolve().parent)
    client = Dograh()
    backup(client, workflow_id, directory)
    body = {
        "workflow_definition": snapshot["workflow_definition"],
        **{k: snapshot[k] for k in SETTINGS},
    }
    fetched = publish(client, workflow_id, body)
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
        "call_inbound": {
            "from_number": "+12025550100",
            "to_number": args.to_number,
            "workflow_run_id": "0",
        }
    }
    status, data = request(
        "POST", runtime + "/api/voice/pre-call", {"X-Dograh-Secret": secret}, body
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
        "POST", runtime + "/api/voice/pre-call", {"X-Dograh-Secret": wrong}, body
    )
    print(status)
    require(status in (401, 403), "Wrong-secret probe expected HTTP 401/403")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("plan", "probe"), default="plan")
    parser.add_argument("--apply", action="store_true")
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
        if args.command == "probe":
            require(
                not args.apply and not args.rollback,
                "Probe cannot apply or roll back workflows",
            )
            require(
                args.runtime_origin and args.to_number,
                "Probe needs --runtime-origin and --to-number",
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
    except (OSError, ValueError, KeyError, TypeError):
        print(
            "Error: invalid local data or API response (details suppressed)",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
