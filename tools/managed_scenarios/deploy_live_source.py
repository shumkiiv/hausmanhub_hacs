#!/usr/bin/env python3
"""Update one managed Node-RED function through the Supervisor ingress API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import urllib.request


SUPERVISOR_BASE_URL = "http://supervisor"
NODE_RED_ADDON_SLUG = "a0d7b954_nodered"


def _request(
    method: str,
    path: str,
    *,
    token: str,
    body: object | None = None,
    ingress_session: str | None = None,
) -> tuple[int, object | None]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if ingress_session is not None:
        headers["Cookie"] = f"ingress_session={ingress_session}"
        headers["Node-RED-API-Version"] = "v2"
    encoded = None
    if body is not None:
        encoded = json.dumps(body, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        SUPERVISOR_BASE_URL + path,
        data=encoded,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read()
        return response.status, json.loads(raw) if raw else None


def _data(payload: object | None) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise RuntimeError("Supervisor returned an invalid response")
    nested = payload.get("data")
    return nested if isinstance(nested, dict) else payload


def deploy(flow_id: str, function_id: str, source_path: Path) -> dict[str, object]:
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        raise RuntimeError("SUPERVISOR_TOKEN is unavailable")
    source = source_path.read_text()
    if not source.startswith("// HAUSMAN_MANAGED_SCENARIO "):
        raise RuntimeError("managed scenario source marker is invalid")

    _, info = _request(
        "GET", f"/addons/{NODE_RED_ADDON_SLUG}/info", token=token
    )
    addon = _data(info)
    ingress_entry = str(
        addon.get("ingress_entry") or addon.get("ingress_url") or ""
    )
    ingress_token = ingress_entry.rstrip("/").split("/")[-1]
    if not ingress_token:
        raise RuntimeError("Node-RED ingress entry is unavailable")

    _, session_payload = _request("POST", "/ingress/session", token=token, body={})
    session = _data(session_payload).get("session")
    if not isinstance(session, str) or not session:
        raise RuntimeError("Node-RED ingress session is unavailable")

    path = f"/ingress/{ingress_token}/flow/{flow_id}"
    _, flow_payload = _request(
        "GET", path, token=token, ingress_session=session
    )
    if not isinstance(flow_payload, dict):
        raise RuntimeError("Node-RED returned an invalid flow")
    nodes = flow_payload.get("nodes")
    if not isinstance(nodes, list):
        raise RuntimeError("Node-RED flow has no nodes")
    function = next(
        (
            node
            for node in nodes
            if isinstance(node, dict) and node.get("id") == function_id
        ),
        None,
    )
    if function is None or function.get("type") != "function":
        raise RuntimeError("managed Node-RED function is unavailable")
    function["func"] = source

    status, _ = _request(
        "PUT",
        path,
        token=token,
        body=flow_payload,
        ingress_session=session,
    )
    _, verified_payload = _request(
        "GET", path, token=token, ingress_session=session
    )
    if not isinstance(verified_payload, dict):
        raise RuntimeError("Node-RED flow verification failed")
    verified_nodes = verified_payload.get("nodes")
    if not isinstance(verified_nodes, list):
        raise RuntimeError("Node-RED flow verification has no nodes")
    actual = next(
        (
            node.get("func")
            for node in verified_nodes
            if isinstance(node, dict) and node.get("id") == function_id
        ),
        None,
    )
    expected_hash = hashlib.sha256(source.encode()).hexdigest()
    actual_hash = (
        hashlib.sha256(actual.encode()).hexdigest()
        if isinstance(actual, str)
        else None
    )
    if actual_hash != expected_hash:
        raise RuntimeError("Node-RED function hash does not match the source")
    return {
        "flowId": flow_id,
        "functionId": function_id,
        "status": status,
        "sourceHash": actual_hash,
        "verified": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow-id", required=True)
    parser.add_argument("--function-id", required=True)
    parser.add_argument("--source", required=True, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            deploy(args.flow_id, args.function_id, args.source),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
