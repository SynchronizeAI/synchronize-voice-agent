"""Back up, merge template deltas, wire and publish Dograh reception workflows.

Successor to the proof workspace's deploy_live.py: the same exact-once line/word
merge, with local git refs, explicit targets, credential wiring and rollback.
One run also creates or updates the caller-work HTTP tool (by its unique name),
resolves its UUID into the nodes and applies, of the workflow settings, only the
template's delta of the ones it owns (SETTINGS_KEYS and the model override's
MODEL_KEYS, with each model connection named by its placeholder resolved by name).
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
# Templates name objects that do not exist yet by UUIDs of this all-zero family
# (the tool specs' placeholder_uuid); none of them may ever be published.
PLACEHOLDER_UUID_PREFIX = "00000000-0000-4000-8000-"
PLACEHOLDER_ORIGIN = "https://synchronize.example.invalid"
PRE_CALL_ROUTE = "/api/voice/pre-call"
TOOL_ROUTE = "/api/voice/tools/caller-work"
TOOL_FIELDS = ("name", "description", "category", "icon", "icon_color", "definition")
# scenarios_caller.py gives every tool it creates (the failing lookup copy) this icon. A copy keeps the
# live tool's name, because that name is the function the model calls; the icon is the one field neither the model
# (custom_tool.py:156 builds the function from name and description) nor Dograh's UI (it draws a tool's icon from its
# category, ui/src/app/tools/config.tsx) reads, so the mark changes nothing a call does. Go-live never takes a marked
# tool for the line's own (tool_change): an eval copy is archived when its run ends.
TEST_COPY_ICON = "synchronize-test-copy"
TOOL_UPDATE_FIELDS = ("description", "icon", "icon_color", "definition")
DISPOSITIONS = "settings.call_dispositions"
# The settings go-live owns. Each takes only its old-ref -> new-ref template delta, like a node field; every other
# setting stays. Of the model override (Dograh 1.51 ModelConfigurationOverride) it owns the services the template
# sets; a named configuration the line selects (model_configuration_uuid), and with it the voice, stays the operator's.
SETTINGS_KEYS = ("call_dispositions", "max_call_duration")
MODEL_OVERRIDE = "model_configuration_override"
MODEL_KEYS = ("llm", "llm_fallback", "stt")
# The provider connections a settings template names by a reserved placeholder UUID (the tool placeholders' family):
# the Dograh provider each must be and the name the operator gave it in Dograh. The connection holds the key, typed
# at a silent read (runbook step 5), never in git; go-live writes only its UUID, and no placeholder is ever published.
MODEL_CONNECTIONS = {
    "00000000-0000-4000-8000-000000000201": ("google", "synchronize-gemini"),
    "00000000-0000-4000-8000-000000000202": ("aws_bedrock", "synchronize-bedrock"),
    "00000000-0000-4000-8000-000000000203": ("deepgram", "synchronize-deepgram"),
}
CONNECTIONS_ROUTE = "/model-connections/provider-connections"
BACKUP_FORMAT = 3  # 3 adds the caller-work tool; format 2 backups stay restorable.
PROBE_RUN_ID = -2147483647  # Receiver ignores run ids; cannot identify a real call.
# Largest digit-text id accepted by the runtime's _run_id validator. Unlike
# "0", this exercises the unknown-run lookup path rather than invalid-body handling.
PROBE_COMPLETION_RUN_ID = "2147483647"  # Largest id Dograh's 32-bit column can hold; never a real run.
# The lookup tool's whole request body. At 1.51 a body template sends only its own keys, and
# the namespaced initial_context it reads is the run's, set after the model's arguments
# (api/services/workflow/tools/custom_tool.py:472-482); without it the body is the model's
# arguments laid over the presets (:432, :473-474), so a caller could name another run.
def run_body(lang):
    """The lookup tool's whole request body: the run id from the run's own context, and the
    workflow's language as a constant (Synchronize speaks visit days in the call's language, not
    in the language the company reads the app in)."""
    return {"workflow_run_id": "{{initial_context.workflow_run_id}}", "language": lang}
# The preset stays as a fail-closed guard. Were the run's context ever without the id, the
# template would fall back to the bare name (api/utils/template_renderer.py:192-196), which
# the model's arguments supply; the required preset refuses that request first
# (custom_tool.py:263-269, :407-427).
RUN_PRESET = [{
    "name": "workflow_run_id",
    "type": "string",
    "value_template": "{{initial_context.workflow_run_id}}",
    "required": True,
}]
MISSING = object()


def pre_call_url(runtime, lang):
    """The Start node's pre-call URL. Dograh 1.51's pre-call body is fixed (pre_call_fetch.py:109-123),
    so the URL carries the workflow's language as a constant, like the lookup's body template does;
    Synchronize words hours_today and next_open only in the language it names (voice_api.py
    _call_language, voice_hours.business_context) and sends neither without it."""
    return f"{runtime}{PRE_CALL_ROUTE}?language={lang}"


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
        listing = path.split("?", 1)[0] in ("/credentials/", "/tools/", CONNECTIONS_ROUTE)
        expected = list if method == "GET" and listing else dict
        if not isinstance(result, expected):
            error = GoLiveError if method == "GET" else UncertainWrite
            raise error(
                f"Dograh {method} returned a malformed response (expected JSON {expected.__name__})"
                + ("; unknown, may have committed" if method != "GET" else "")
            )
        return result


def git_file(ref, name, optional=False):
    """A workflows/ file at a local commit; None when optional and absent there."""
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
    commit = resolved.stdout.strip()
    path = f"{commit}:synchronize/workflows/{name}"
    if optional:
        # Trees only: absence is an empty listing, any git failure an error.
        listed = subprocess.run(
            ["git", "-C", str(ROOT), "ls-tree", "--name-only", commit, "--",
             f"synchronize/workflows/{name}"],
            capture_output=True,
            text=True,
        )
        require(listed.returncode == 0, "Cannot read template from local git ref")
        if not listed.stdout.strip():
            return None
    result = subprocess.run(
        ["git", "-C", str(ROOT), "show", path], capture_output=True, text=True
    )
    require(result.returncode == 0, "Cannot read template from local git ref")
    return json.loads(result.stdout)


def template(ref, lang):
    return git_file(ref, f"consultation-{lang}.json")["workflow_definition"]


def settings_template(ref, lang):
    return git_file(ref, f"consultation-{lang}-settings.json")["workflow_configurations"]


def tool_spec(ref, lang):
    """The caller-work HTTP tool at a ref, or None for refs from before the tool."""
    spec = git_file(ref, f"tool-lookup-caller-work-{lang}.json", optional=True)
    if spec is None:
        return None
    placeholder = str(UUID(spec["placeholder_uuid"]))
    require(
        placeholder.startswith(PLACEHOLDER_UUID_PREFIX) and placeholder != PENDING_UUID,
        "Tool placeholder_uuid must be a reserved placeholder UUID",
    )
    tool = spec["tool"]
    config = tool["definition"]["config"]
    require(
        tool["category"] == tool["definition"]["type"] == "http_api"
        and config["url"] == PLACEHOLDER_ORIGIN + TOOL_ROUTE
        and config["credential_uuid"] == "<DOGRAH_WEBHOOK_CREDENTIAL_UUID>"
        and set(tool) == set(TOOL_FIELDS),
        "Tool template must be an HTTP API tool on the placeholder caller-work route",
    )
    return {"placeholder_uuid": placeholder, "tool": tool}


def resolve_tool_refs(definition, placeholder, tool_uuid):
    """Replace a template's placeholder tool UUID in every node's tool_uuids."""
    out = copy.deepcopy(definition)
    for node in out["nodes"]:
        refs = node.get("data", {}).get("tool_uuids")
        if isinstance(refs, list):
            node["data"]["tool_uuids"] = [
                tool_uuid if ref == placeholder else ref for ref in refs
            ]
    return out


def references(definition, tool_uuid):
    return any(
        tool_uuid in (node.get("data", {}).get("tool_uuids") or [])
        for node in definition["nodes"]
    )


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


def edge_index(definition):
    """Edges with an id; a template edge must have one to be merged."""
    edges = [edge for edge in definition.get("edges", []) if "id" in edge]
    result = {edge["id"]: edge for edge in edges}
    require(len(result) == len(edges), "Duplicate edge ids")
    return result


def patch_fields(a, b, target, prefix, field_modes):
    """Merge the template delta a -> b of one node's or edge's data into target."""
    for field in sorted(a.keys() | b.keys()):
        ov, nv = a.get(field, MISSING), b.get(field, MISSING)
        if ov == nv:
            continue
        label = f"{prefix}.data.{field}"
        live_value = target.get(field, MISSING)
        value = patch_value(ov, nv, live_value, label)
        if field_modes is not None and value != live_value:
            field_modes[label] = (
                "template-replaced" if live_value == ov else "MERGED-INTO-CUSTOM"
            )
        if value is MISSING:
            target.pop(field, None)
        else:
            target[field] = value


def template_fields(old, new, wired=()):
    """Every node/edge data field whose template value changes, as printed.

    wired: node ids whose tool_uuids is caller-work tool wiring (wire_tool_refs).
    """
    fields = set()
    for kind, index in (("node", nodes), ("edge", edge_index)):
        before, after = index(old), index(new)
        for item_id in before.keys() & after.keys():
            if kind == "node" and item_id == "6":
                continue
            a, b = before[item_id].get("data", {}), after[item_id].get("data", {})
            fields |= {
                f"{kind} {item_id}.data.{field}"
                for field in a.keys() | b.keys()
                if a.get(field, MISSING) != b.get(field, MISSING)
                and not (kind == "node" and item_id == "1" and field in START_FIELDS)
                and not (kind == "node" and item_id in wired and field == "tool_uuids")
            }
    return fields


def patched(live, old, new, runtime, credential_uuid, lang, field_modes=None, wired=()):
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
        # Wiring always uses operator values, never template placeholders. The
        # caller-work tool reference is wiring too: wire_tool_refs sets it.
        skip = (START_FIELDS if node_id == "1" else set()) | (
            {"tool_uuids"} if node_id in wired else set()
        )
        patch_fields(
            {k: v for k, v in a["data"].items() if k not in skip},
            {k: v for k, v in b["data"].items() if k not in skip},
            target["data"],
            f"node {node_id}",
            field_modes,
        )
    live_edges, old_edges, new_edges = edge_index(out), edge_index(old), edge_index(new)
    require(
        len(old_edges) == len(old.get("edges", []))
        and len(new_edges) == len(new.get("edges", [])),
        "Template edges must have ids",
    )
    for edge_id in sorted(old_edges.keys() | new_edges.keys()):
        a, b = old_edges.get(edge_id), new_edges.get(edge_id)
        if a == b:
            continue
        require(
            a is not None and b is not None and edge_id in live_edges,
            f"edge {edge_id}: template/live structure differs",
        )
        target = live_edges[edge_id]
        require(
            {k: v for k, v in a.items() if k != "data"}
            == {k: v for k, v in b.items() if k != "data"}
            and all(a.get(k) == target.get(k) for k in ("source", "target")),
            f"edge {edge_id}: only edge data changes can be merged",
        )
        patch_fields(
            a.get("data", {}),
            b.get("data", {}),
            target.setdefault("data", {}),
            f"edge {edge_id}",
            field_modes,
        )
    require(before["1"]["type"] == "startCall", "node 1: expected Start Call")
    require(
        current["1"]["data"]["pre_call_fetch_mode"] == "inbound",
        "Template must use inbound pre-call fetch",
    )
    before["1"]["data"].update(
        pre_call_fetch_mode=current["1"]["data"]["pre_call_fetch_mode"],
        pre_call_fetch_url=pre_call_url(runtime, lang),
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
    validate_wiring(out, live, runtime, credential_uuid, lang)
    return out


def validate_wiring(definition, original, runtime, credential_uuid, lang, final=False):
    """final: after the credential/tool writes, no placeholder UUID may remain."""
    indexed = nodes(definition)
    require(nodes(original).keys() <= indexed.keys(), "A pre-existing node was lost")
    expected = {
        "pre_call_fetch_mode": "inbound",
        "pre_call_fetch_url": pre_call_url(runtime, lang),
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
        ".invalid" not in text
        and "<dograh" not in text
        and not (final and PLACEHOLDER_UUID_PREFIX in text),
        "Unresolved placeholder in workflow",
    )


def planned_tool(spec, runtime, credential_uuid):
    tool = copy.deepcopy(spec["tool"])
    tool["definition"]["config"].update(
        url=runtime + TOOL_ROUTE, credential_uuid=credential_uuid
    )
    return tool


def validate_tool(tool, runtime, credential_uuid):
    config = tool["definition"]["config"]
    require(
        tool["category"] == "http_api"
        and config["url"] == runtime + TOOL_ROUTE
        and config["credential_uuid"] == credential_uuid,
        "Tool wiring verification failed",
    )
    text = json.dumps(tool).lower()
    require(
        ".invalid" not in text
        and "<dograh" not in text
        and PLACEHOLDER_UUID_PREFIX not in text,
        "Unresolved placeholder in tool",
    )


def tool_snapshot(tool):
    """The tool fields go-live owns, plus its identity and status."""
    return {
        "tool_uuid": str(UUID(tool["tool_uuid"])),
        "status": tool["status"],
        **{field: tool[field] for field in TOOL_FIELDS},
    }


def lookup_tool(client, name):
    matches = [tool for tool in client.api("GET", "/tools/") if tool["name"] == name]
    require(
        len(matches) <= 1,
        f"Tool name {name} is ambiguous; archive the duplicate in the Dograh UI first",
    )
    return tool_snapshot(matches[0]) if matches else None


def tool_change(existing, planned):
    if existing is None:
        return "create"
    require(
        existing["icon"] != TEST_COPY_ICON,
        f"Tool {planned['name']} is a copy scenarios_caller.py made for an eval run, not the line's own: let that run "
        "finish, or run scenarios_caller.py cleanup for a run that crashed, then re-plan",
    )
    require(
        existing["status"] == "active" and existing["category"] == planned["category"],
        f"Existing tool {planned['name']} must be an active HTTP API tool",
    )
    differs = [f for f in TOOL_UPDATE_FIELDS if existing[f] != planned[f]]
    return "update" if differs else None


def tool_label(name):
    return f"tool.{name}"


def tool_owned(tool):
    """The tool fields a template owns; the URL and credential are go-live's wiring."""
    owned = copy.deepcopy({field: tool[field] for field in TOOL_UPDATE_FIELDS})
    config = owned["definition"].get("config")
    if isinstance(config, dict):
        config.pop("url", None)
        config.pop("credential_uuid", None)
    return owned


def tool_mode(existing, old_planned, planned):
    """Like the field modes: only a tool still equal to a template is replaced unasked."""
    if tool_owned(existing) == tool_owned(planned):
        return "wiring"
    if old_planned is not None and tool_owned(existing) == tool_owned(old_planned):
        return "template-replaced"
    return "REPLACES-CUSTOM"


def wire_tool_refs(client, definition, templates, spec, tool_ref):
    """The caller-work tool reference is wiring, like the URLs.

    Every node the new template gives the tool references tool_ref once. A reference to an
    inactive tool of the same name (archived in the Dograh UI) is replaced where it stands;
    other tools on the node stay; a reference Dograh cannot return is refused. patched()
    leaves these nodes' tool_uuids alone, so a template change to their other tools is
    refused here rather than lost. Read-only.
    """
    name, placeholder = spec["tool"]["name"], spec["placeholder_uuid"]
    literal = {
        ref
        for template in templates
        for node in template["nodes"]
        for ref in node.get("data", {}).get("tool_uuids") or []
        if not ref.startswith(PLACEHOLDER_UUID_PREFIX)
    }

    def others(node):
        return [
            ref
            for ref in node.get("data", {}).get("tool_uuids") or []
            if not ref.startswith(PLACEHOLDER_UUID_PREFIX)
        ]

    out = copy.deepcopy(definition)
    indexed = nodes(out)
    for node_id, node in nodes(templates[-1]).items():
        if placeholder not in (node.get("data", {}).get("tool_uuids") or []):
            continue
        require(
            others(nodes(templates[0])[node_id]) == others(node),
            f"node {node_id}: the template changes other tools on the caller-work node; "
            "go-live wires only the caller-work tool there",
        )
        data = indexed[node_id]["data"]
        refs = []
        for ref in data.get("tool_uuids") or []:
            if ref != tool_ref and ref not in literal and not ref.startswith(PLACEHOLDER_UUID_PREFIX):
                try:
                    other = client.api("GET", f"/tools/{ref}")
                except UncertainWrite:
                    raise
                except GoLiveError:
                    raise GoLiveError(
                        f"node {node_id}: tool reference {ref} is not a tool Dograh returns; remove it "
                        "from the node in the Dograh UI, publish, then re-plan"
                    ) from None
                if other.get("name") == name and other.get("status") != "active":
                    ref = tool_ref
            if ref != tool_ref or ref not in refs:
                refs.append(ref)
        if tool_ref not in refs:
            refs.append(tool_ref)
        data["tool_uuids"] = refs
    return out


def show_tool(planned, existing, change, mode=None):
    name = planned["name"]
    if change is None:
        print(f"tool {name}: unchanged")
        return
    if change == "create":
        print(f"tool {name}: create (route {TOOL_ROUTE}; credential reference only)")
        return
    differs = [f for f in TOOL_UPDATE_FIELDS if existing[f] != planned[f]]
    if "definition" in differs:
        old_def, new_def = existing["definition"], planned["definition"]
        differs.remove("definition")
        differs += [
            f"definition.{key}"
            for key in sorted(old_def.keys() | new_def.keys())
            if key != "config" and old_def.get(key) != new_def.get(key)
        ] + [
            f"definition.config.{key}"
            for key in sorted(
                old_def.get("config", {}).keys() | new_def.get("config", {}).keys()
            )
            if old_def.get("config", {}).get(key) != new_def.get("config", {}).get(key)
        ]
    print(f"tool {name}: update {', '.join(differs)} ({mode}; values withheld)")
    if mode == "REPLACES-CUSTOM":
        print(
            f"  the live tool equals neither template (edited in Dograh → Tools?); apply overwrites it "
            f"only with --accept-merged {tool_label(name)}"
        )


def settings_values(settings):
    """The settings values go-live owns, by the label a plan prints (MISSING when unset)."""
    settings = settings or {}
    override = settings.get(MODEL_OVERRIDE) or {}
    return {
        **{f"settings.{key}": settings.get(key, MISSING) for key in SETTINGS_KEYS},
        **{f"settings.{MODEL_OVERRIDE}.{key}": override.get(key, MISSING) for key in MODEL_KEYS},
    }


def model_connections(client, *templates):
    """Each connection placeholder the settings templates name, mapped to the UUID of the one active
    connection of its name and provider. Read-only; refused before any write when one is missing."""
    text = json.dumps(templates)
    wanted = {ref: spec for ref, spec in MODEL_CONNECTIONS.items() if ref in text}
    if not wanted:
        return {}
    listed = client.api("GET", CONNECTIONS_ROUTE)
    resolved = {}
    for ref, (provider, name) in sorted(wanted.items()):
        matches = [entry for entry in listed if entry["name"] == name]
        require(
            matches,
            f"Model connection {name} ({provider}) is missing: add it in Dograh (runbook step 5), then re-plan",
        )
        require(
            len(matches) == 1,
            f"Model connection name {name} is ambiguous; archive the duplicate in Dograh first",
        )
        require(
            matches[0]["provider"] == provider and matches[0].get("is_active", True) is True,
            f"Model connection {name} must be an active {provider} connection",
        )
        resolved[ref] = str(UUID(matches[0]["uuid"]))
    return resolved


def resolve_connections(value, resolved):
    """A settings template with each connection placeholder replaced by its connection's UUID."""
    if isinstance(value, dict):
        return {key: resolve_connections(item, resolved) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_connections(item, resolved) for item in value]
    return resolved.get(value, value) if isinstance(value, str) else value


def planned_settings(live, old, new, field_modes):
    """Apply only the template's delta of the settings go-live owns to the live settings."""
    out = copy.deepcopy(live or {})
    before, previous, current = settings_values(live), settings_values(old), settings_values(new)
    for label, new_value in current.items():
        old_value, live_value = previous[label], before[label]
        if old_value == new_value or live_value == new_value:
            continue
        require(new_value is not MISSING, f"Template settings must keep {label.removeprefix('settings.')}")
        field_modes[label] = (
            "template-replaced"
            if live_value == old_value
            else "set" if live_value in (MISSING, None, []) else "REPLACES-CUSTOM"
        )
        key = label.rsplit(".", 1)[1]
        target = out
        if label != f"settings.{key}":
            # An override that was null or absent becomes one holding only the template's services.
            target = out[MODEL_OVERRIDE] = dict(out.get(MODEL_OVERRIDE) or {})
        target[key] = copy.deepcopy(new_value)
    return out


def show_settings(live, planned, field_modes):
    before, after = settings_values(live), settings_values(planned)
    for label in after:
        if label not in field_modes:
            continue
        if label == DISPOSITIONS:
            codes = [
                ", ".join(item["code"] for item in (value if value is not MISSING else None) or [])
                or "none"
                for value in (before[label], after[label])
            ]
            print(f"{DISPOSITIONS}: {field_modes[DISPOSITIONS]}; codes")
            print(f"  - {codes[0]}\n  + {codes[1]}\n  (descriptions withheld)")
            continue
        # Models, connection UUIDs and a duration: no secret is part of these values.
        shown = ["unset" if value is MISSING else json.dumps(value) for value in (before[label], after[label])]
        print(f"{label}: {field_modes[label]}")
        print(f"  - {shown[0]}\n  + {shown[1]}")


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


def backup(client, workflow_id, directory, *, allow_draft=False, tool_names=()):
    workflow = client.api("GET", f"/workflow/fetch/{workflow_id}")
    require(workflow["id"] == workflow_id, "Fetched workflow id mismatch")
    if not allow_draft:
        require_published(workflow, workflow_id)
    snapshot = {
        "format_version": BACKUP_FORMAT,
        "workflow_id": workflow_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "workflow_definition": workflow["workflow_definition"],
        **{
            field: workflow[field]
            for field in ("version_status", "version_number", "current_definition_id")
        },
        **{field: workflow[field] for field in SETTINGS},
        # None records that the named tool did not exist at backup time.
        "tools": {name: lookup_tool(client, name) for name in tool_names},
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
    for name, tool in snapshot["tools"].items():
        print(f"Tool {name}: {'absent' if tool is None else tool['tool_uuid']}")
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
    for kind, index in (("node", nodes), ("edge", edge_index)):
        previous, current = index(before), index(after)
        for item_id in previous.keys() - current.keys():
            changes.append(
                (f"{kind} {item_id}", f"[removed {kind}]", previous[item_id].get("type"), None)
            )
        for item_id, item in current.items():
            where = f"{kind} {item_id}"
            if item_id not in previous:
                changes.append((where, f"[added {kind}]", None, item.get("type")))
                if kind == "node" and item_id == "6":
                    for field in ("endpoint_url", "credential_uuid"):
                        changes.append((where, field, None, item["data"][field]))
                continue
            a, b = previous[item_id].get("data", {}), item.get("data", {})
            for field in sorted(a.keys() | b.keys()):
                if a.get(field) != b.get(field) or (field in a) != (field in b):
                    changes.append((where, field, a.get(field), b.get(field)))
    return changes


def show_plan(changes, field_modes=None, lang=None):
    """lang: the line's language for a plan; None for a rollback, which restores the backup's URLs."""
    for where, field, old, new in changes:
        label = f"{where}.data.{field}"
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
                pre_call_url("", lang)
                if field == "pre_call_fetch_url"
                else "/api/voice/call-ended"
            )
            print(
                f"  - {'configured URL' if old else 'unset'}\n  + "
                + (f"runtime origin + {path}" if lang else "backup URL")
            )
        elif field in START_FIELDS | {
            "credential_uuid",
            "tool_uuids",
            "[added node]",
            "[removed node]",
            "[added edge]",
            "[removed edge]",
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


def verify_snapshot(workflow, expected_definition, expected):
    """expected holds the SETTINGS values the published release must carry."""
    require(
        workflow["workflow_definition"] == expected_definition,
        "Published definition differs from planned definition",
    )
    for field in SETTINGS:
        require(workflow[field] == expected[field], f"{field}: changed during publish")
    require(workflow.get("version_status") == "published", "Workflow is not published")


def verify_tool(client, tool_uuid, expected):
    fetched = tool_snapshot(client.api("GET", f"/tools/{tool_uuid}"))
    require(
        fetched["tool_uuid"] == tool_uuid
        and fetched["status"] == expected.get("status", "active")
        and all(fetched[field] == expected[field] for field in TOOL_FIELDS),
        "Tool read-back differs from the planned tool",
    )
    return fetched


def summary(status, workflow_id, changes, existing_count, settings=None):
    print(
        f"{status} workflow {workflow_id}: {len(changes)} field/node changes; {existing_count} existing nodes preserved"
    )
    grouped = {}
    for where, field, _, _ in changes:
        grouped.setdefault(where, []).append(field)
    for where, fields in grouped.items():
        print(f"  {where}: {', '.join(fields)}")
    if settings:
        print(f"  settings: {settings}")


class WriteState:
    """Keep recovery evidence even when the request's outcome is uncertain."""

    def __init__(self, path, draft="no"):
        self.path, self.stage = path, "pre-write"
        self.credential, self.published, self.draft = "unchanged", "unchanged", draft
        self.tool = "unchanged"
        self.attempted = False

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        if error is not None and self.attempted:
            print(
                f"RECOVERY — failed stage: {self.stage}\n"
                f"  credential: {self.credential}\n"
                f"  tool: {self.tool}\n"
                f"  live published definition: {self.published}\n"
                f"  draft left behind: {self.draft}",
                file=sys.stderr,
            )
            # An updated tool is live at once for every workflow that references it.
            tool_live = self.tool not in ("unchanged", "created")
            workflow_unchanged = self.published == "unchanged" and self.draft == "no"
            if workflow_unchanged and not tool_live:
                print(
                    "  No workflow rollback needed: published definition unchanged; no draft saved. "
                    "A credential change or a newly created tool is retained.",
                    file=sys.stderr,
                )
            else:
                if workflow_unchanged:
                    print(
                        f"  The published workflow already uses the changed tool ({self.tool}); "
                        "this restores the backed-up tool:",
                        file=sys.stderr,
                    )
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
                elif stage.startswith("tool"):
                    self.tool = "unknown, may have been written"
                elif stage == "draft save":
                    self.draft = "unknown, may have committed"
                elif stage == "publish":
                    self.published = "unknown, may have committed"
                    self.draft = "unknown"
            raise
        if stage.startswith("credential"):
            self.credential = outcome
        elif stage.startswith("tool"):
            self.tool = outcome
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
    old_settings = settings_template(args.old_ref, args.lang)
    new_settings = settings_template(args.new_ref, args.lang)
    spec, old_spec = tool_spec(args.new_ref, args.lang), tool_spec(args.old_ref, args.lang)
    require(
        not old_spec or (spec and old_spec["tool"]["name"] == spec["tool"]["name"]),
        "Removing or renaming the caller-work tool is not supported by go-live",
    )
    if spec:
        require(
            references(new, spec["placeholder_uuid"]),
            "Template does not reference its tool placeholder",
        )
        config = spec["tool"]["definition"]["config"]
        require(
            config.get("body_template") == run_body(args.lang)
            and config.get("preset_parameters") == RUN_PRESET,
            "Tool template must send only the run id, from its body template, and its language",
        )
    client = Dograh()
    snapshot, path = backup(
        client,
        args.workflow_id,
        backup_directory(args.backup_dir),
        tool_names=(spec["tool"]["name"],) if spec else (),
    )
    live, live_settings = snapshot["workflow_definition"], snapshot["workflow_configurations"]
    # The model connections the settings templates name, as their UUIDs, before any comparison with live.
    connections = model_connections(client, old_settings, new_settings)
    old_settings = resolve_connections(old_settings, connections)
    new_settings = resolve_connections(new_settings, connections)
    existing = find_credential(client, args.credential_name)
    existing_tool = change = tool = None
    field_modes = {}
    templates = (old, new)
    wired = set()
    if spec:
        wired = {
            node["id"]
            for node in new["nodes"]
            if spec["placeholder_uuid"] in (node.get("data", {}).get("tool_uuids") or [])
        }
        existing_tool = snapshot["tools"][spec["tool"]["name"]]
        tool = planned_tool(spec, runtime, existing or PENDING_UUID)
        change = tool_change(existing_tool, tool)
        if change == "update":
            old_tool = planned_tool(old_spec, runtime, existing or PENDING_UUID) if old_spec else None
            field_modes[tool_label(tool["name"])] = tool_mode(existing_tool, old_tool, tool)
        # Until the tool exists, the template placeholder stands for it.
        tool_ref = existing_tool["tool_uuid"] if existing_tool else spec["placeholder_uuid"]
        new = resolve_tool_refs(new, spec["placeholder_uuid"], tool_ref)
        if old_spec:
            old = resolve_tool_refs(old, old_spec["placeholder_uuid"], tool_ref)
    planned = patched(live, old, new, runtime, existing or PENDING_UUID, args.lang, field_modes, wired)
    if spec:
        planned = wire_tool_refs(client, planned, templates, spec, tool_ref)
    settings = planned_settings(live_settings, old_settings, new_settings, field_modes)
    require(PLACEHOLDER_UUID_PREFIX not in json.dumps(settings), "Unresolved placeholder in settings")
    for value in (planned, settings, tool):
        ensure_no_secrets(value)
    changes = diff_fields(live, planned)
    show_plan(changes, field_modes, args.lang)
    show_settings(live_settings, settings, field_modes)
    if spec:
        show_tool(tool, existing_tool, change, field_modes.get(tool_label(tool["name"])))
    merged = {
        field
        for field, mode in field_modes.items()
        if mode in ("MERGED-INTO-CUSTOM", "REPLACES-CUSTOM")
    }
    old_values = settings_values(old_settings)
    known_fields = template_fields(old, new, wired) | {
        label for label, value in settings_values(new_settings).items() if old_values[label] != value
    } | {label for label in field_modes if label.startswith("tool.")}
    require(
        set(args.accept_merged) <= known_fields,
        "--accept-merged must name a changed template field exactly as printed",
    )
    settings_changed = settings != live_settings
    already_wired = (
        planned == live and not settings_changed and change is None and existing is not None
    )
    print(
        "Already wired; no remote writes"
        if already_wired
        else "Would create/update credential (secret not shown)"
    )
    settings_note = ", ".join(
        label.removeprefix("settings.") for label in field_modes if label.startswith("settings.")
    ) or None
    if not args.apply:
        summary("PLAN", args.workflow_id, changes, len(nodes(live)), settings_note)
        print("No remote writes")
        return
    unaccepted = merged - set(args.accept_merged)
    require(
        not unaccepted,
        "Review the MERGED-INTO-CUSTOM/REPLACES-CUSTOM diff, then explicitly accept each field: "
        + " ".join(f"--accept-merged {shlex.quote(field)}" for field in sorted(unaccepted)),
    )
    # Fresh backup and all exact-once patches must succeed before ANY write,
    # including credential rotation. Also detect concurrent workflow edits.
    fresh = client.api("GET", f"/workflow/fetch/{args.workflow_id}")
    require_published(fresh, args.workflow_id)
    require(same_snapshot(fresh, snapshot), "Workflow changed since backup; rerun plan")
    if spec:
        require(
            lookup_tool(client, spec["tool"]["name"]) == existing_tool,
            "Tool changed since backup; rerun plan",
        )
    require(path.is_file(), "Fresh backup missing")
    print(f"ROLLBACK FILE: {path}")
    if already_wired:
        return
    with WriteState(path) as state:
        ident = credential(client, args.credential_name, secret, existing, state)
        # Only substitute UUIDs: do not re-run the merge after a remote write.
        if spec:
            tool = planned_tool(spec, runtime, ident)
            validate_tool(tool, runtime, ident)
            written = None
            if existing_tool is None:
                written = state.write(
                    "tool create", "created", lambda: client.api("POST", "/tools/", tool)
                )
            elif tool_change(existing_tool, tool):
                written = state.write(
                    "tool update",
                    "updated",
                    lambda: client.api(
                        "PUT",
                        f"/tools/{existing_tool['tool_uuid']}",
                        {field: tool[field] for field in TOOL_UPDATE_FIELDS},
                    ),
                )
            tool_uuid = (
                str(UUID(written["tool_uuid"])) if written else existing_tool["tool_uuid"]
            )
            state.stage = "tool read-back"
            print("Stage: tool read-back")
            verify_tool(client, tool_uuid, tool)
            planned = resolve_tool_refs(planned, spec["placeholder_uuid"], tool_uuid)
        planned_nodes = nodes(planned)
        planned_nodes["1"]["data"]["pre_call_fetch_credential_uuid"] = ident
        planned_nodes["6"]["data"]["credential_uuid"] = ident
        state.stage = "wiring validation"
        print("Stage: wiring validation")
        validate_wiring(planned, live, runtime, ident, args.lang, final=True)
        require(
            not spec or references(planned, tool_uuid),
            "Tool reference missing from the planned definition",
        )
        expected = {
            "workflow_configurations": settings,
            "template_context_variables": snapshot["template_context_variables"],
        }
        if planned != live or settings_changed:
            body = {"workflow_definition": planned}
            if settings_changed:
                body["workflow_configurations"] = settings
            fetched = publish(client, args.workflow_id, body, state)
            verify_snapshot(fetched, planned, expected)
            validate_wiring(fetched["workflow_definition"], live, runtime, ident, args.lang, final=True)
    summary("APPLIED", args.workflow_id, changes, len(nodes(live)), settings_note)
    if planned == live and not settings_changed:
        print("Workflow definition and settings already current; nothing published")
    else:
        print(
            "Published and verified; "
            + (
                f"{settings_note} applied, other settings unchanged"
                if settings_changed
                else "settings unchanged"
            )
        )


def rollback(args):
    snapshot = json.loads(args.rollback.read_text(encoding="utf-8"))
    require(
        snapshot.get("format_version") in (2, BACKUP_FORMAT),
        "Unsupported backup format: require format_version 2 or 3; old backups are refused",
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
    saved_tools = snapshot.get("tools") or {}
    # Recovery must work when a failed apply left a draft. Preserve it for
    # inspection, clearly labelled; never allow that draft backup as a restore target.
    current, _ = backup(
        client, workflow_id, directory, allow_draft=True, tool_names=tuple(saved_tools)
    )
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
    restores = []
    for name, saved in saved_tools.items():
        now = current["tools"][name]
        if saved is None:
            # Like the credential: a tool created later is kept, and the
            # restored definition no longer references it.
            print(f"tool {name}: did not exist at backup time; retained")
        elif now is None or now["tool_uuid"] != saved["tool_uuid"]:
            print(
                f"tool {name}: backed-up tool {saved['tool_uuid']} is not an active tool of that "
                "name any more; restore it in the Dograh UI (Tools)",
                file=sys.stderr,
            )
        elif any(now[field] != saved[field] for field in TOOL_UPDATE_FIELDS):
            restores.append(saved)
            print(f"tool {name}: restore backup definition (values withheld)")
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
        for saved in restores:
            state.write(
                "tool restore",
                "restored",
                lambda saved=saved: client.api(
                    "PUT",
                    f"/tools/{saved['tool_uuid']}",
                    {field: saved[field] for field in TOOL_UPDATE_FIELDS},
                ),
            )
            state.stage = "tool read-back"
            print("Stage: tool read-back")
            verify_tool(client, saved["tool_uuid"], saved)
    print(
        f"ROLLED BACK workflow {workflow_id}: definition and settings equal backup; "
        + ("tool definitions restored; " if restores else "")
        + "credential retained"
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
    wrong = "wrong-" + uuid4().hex
    while wrong == secret:
        wrong = "wrong-" + uuid4().hex
    lang = TARGETS[args.workflow_id]
    for route, url, payload, headers in (
        # The URL go-live wires, with the line's language (pre_call_url).
        ("pre-call", pre_call_url(runtime, lang), body, {"Idempotency-Key": f"dograh-pre-call-{PROBE_RUN_ID}"}),
        ("call-ended", runtime + "/api/voice/call-ended", {"workflow_run_id": PROBE_COMPLETION_RUN_ID}, {}),
        # The tool's body template keeps the run id's integer type (custom_tool.py:278-295).
        (
            "tools/caller-work",
            runtime + TOOL_ROUTE,
            {"workflow_run_id": int(PROBE_COMPLETION_RUN_ID), "language": lang},
            {},
        ),
    ):
        status, data = request(
            "POST",
            url,
            {**headers, "X-Dograh-Secret": secret},
            payload,
        )
        print(status)
        require(
            status == 200 and isinstance(data, dict),
            f"Authenticated {route} probe expected HTTP 200 JSON object",
        )
        if route == "call-ended":
            require(data == {}, "Unknown-run call-ended probe expected empty JSON object")
        if route == "tools/caller-work":
            require(
                data == {"known": False},
                'Unknown-run caller-work probe expected {"known": false}',
            )
        status, _ = request(
            "POST",
            url,
            {**headers, "X-Dograh-Secret": wrong},
            payload,
        )
        print(status)
        require(status in (401, 403), f"Wrong-secret {route} probe expected HTTP 401/403")


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
