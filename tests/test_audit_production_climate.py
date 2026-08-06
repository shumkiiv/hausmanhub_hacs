from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error

from tools.audit_production_climate import (
    ENDPOINTS,
    AccessFileError,
    AdminAccess,
    AuditAuthorizationError,
    AuditRequestError,
    EndpointResult,
    build_summary,
    format_summary,
    load_access,
    http_get_json,
    run_audit,
)


ACCESS = AdminAccess(base_url="http://ha.local:8123", token="test-token")


class _FakeResponse:
    def __init__(self, payload: object, status: int = 200) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="http://ha.local:8123/x",
        code=code,
        msg="error",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b"{}"),
    )


class LoadAccessTest(unittest.TestCase):
    def test_missing_file_is_an_access_error(self) -> None:
        with self.assertRaises(AccessFileError):
            load_access(Path("/nonexistent/ha_admin_access.json"))

    def test_invalid_json_is_an_access_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "access.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(AccessFileError):
                load_access(path)

    def test_missing_token_is_an_access_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "access.json"
            path.write_text(
                json.dumps({"base_url": "http://ha.local:8123"}), encoding="utf-8"
            )
            with self.assertRaises(AccessFileError):
                load_access(path)

    def test_valid_file_loads_and_normalizes_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "access.json"
            path.write_text(
                json.dumps(
                    {"base_url": "http://ha.local:8123/", "token": " abc "}
                ),
                encoding="utf-8",
            )
            access = load_access(path)
        self.assertEqual("http://ha.local:8123", access.base_url)
        self.assertEqual("abc", access.token)


class HttpGetJsonTest(unittest.TestCase):
    def test_sends_bearer_token_with_get(self) -> None:
        captured: list[object] = []

        def opener(request: object, timeout: float) -> _FakeResponse:
            captured.append(request)
            return _FakeResponse({"ok": True})

        result = http_get_json(ACCESS, "/api/config", timeout=5.0, opener=opener)
        self.assertEqual(200, result.status)
        self.assertEqual({"ok": True}, result.payload)
        request = captured[0]
        self.assertEqual("GET", getattr(request, "method"))
        self.assertEqual(
            "Bearer test-token",
            getattr(request, "headers").get("Authorization"),
        )

    def test_auth_failures_raise_authorization_error(self) -> None:
        for code in (401, 403):
            def opener(request: object, timeout: float, code: int = code) -> object:
                raise _http_error(code)

            with self.assertRaises(AuditAuthorizationError):
                http_get_json(ACCESS, "/api/config", timeout=5.0, opener=opener)

    def test_other_http_errors_raise_request_error(self) -> None:
        def opener(request: object, timeout: float) -> object:
            raise _http_error(500)

        with self.assertRaises(AuditRequestError):
            http_get_json(ACCESS, "/api/config", timeout=5.0, opener=opener)

    def test_unreachable_host_raises_request_error(self) -> None:
        def opener(request: object, timeout: float) -> object:
            raise urllib.error.URLError("connection refused")

        with self.assertRaises(AuditRequestError):
            http_get_json(ACCESS, "/api/config", timeout=5.0, opener=opener)


def _fake_results() -> list[EndpointResult]:
    payloads = {
        "core_config": {"version": "2026.7.3", "location_name": "Home"},
        "capabilities": {"contract": {"name": "caps", "version": 1}},
        "climate_mode": {
            "mode": "disabled",
            "contour_configured": True,
            "rollout": {
                "phase": "shadow",
                "enable_allowed": False,
                "commands_enabled": False,
                "canary_room_id": None,
                "managed_room_count": 0,
                "shadow_ready_room_count": 1,
                "shadow_sample_count": 30,
                "reasons": ["canary_room_not_selected"],
            },
            "cutover": {
                "phase": "shadow",
                "node_red_can_be_disabled": False,
                "pending_room_ids": ["living"],
                "reasons": ["native_control_disabled"],
            },
        },
        "climate_readiness": {
            "bridge_mode": "disabled",
            "status": "disabled",
            "ready": False,
            "fresh": False,
            "registry": {"room_count": 5, "device_count": 13},
            "reconciliation": None,
            "reasons": ["bridge_disabled"],
        },
        "climate_registry": {
            "rooms": [{"id": "bathroom"}, {"id": "living"}],
            "devices": [
                {
                    "id": "ac-living",
                    "endpoints": [
                        {"role": "control", "entity_id": "climate.living_secret"}
                    ],
                }
            ],
        },
        "climate_device_bindings": {
            "snapshot_revision": "rev-1",
            "rooms": [
                {
                    "id": "living",
                    "devices": [
                        {
                            "device_id": "ac-living",
                            "current_entity_id": "climate.living_secret",
                            "current_available": True,
                            "candidates": [{"entity_id": "climate.living_secret"}],
                        },
                        {
                            "device_id": "sensor-living",
                            "current_entity_id": None,
                            "current_available": False,
                            "candidates": [],
                        },
                    ],
                }
            ],
        },
        "climate_shadow_comparison": {
            "observed_at": 1754400000000,
            "rooms": [
                {"room_id": "living", "status": "aligned"},
                {"room_id": "bathroom", "status": "diverged"},
            ],
        },
        "climate_shadow_window": {
            "window": {"collection_active": True},
            "summary": {
                "sample_count": 30,
                "room_count": 2,
                "ready_room_count": 1,
                "diverged_room_count": 1,
                "insufficient_room_count": 0,
                "first_observed_at": 1754370000000,
                "latest_observed_at": 1754400000000,
            },
            "rooms": [
                {"room_id": "living", "verdict": "ready", "reasons": []},
                {
                    "room_id": "bathroom",
                    "verdict": "diverged",
                    "reasons": ["diverged"],
                },
            ],
            "samples": [],
        },
    }
    return [
        EndpointResult(name=name, path=path, status=200, payload=payloads[name])
        for name, path in ENDPOINTS
    ]


class BuildSummaryTest(unittest.TestCase):
    def test_summary_reports_rollout_readiness_bindings_and_shadow(self) -> None:
        summary = build_summary(_fake_results())
        self.assertEqual("disabled", summary["climate_mode"]["mode"])
        self.assertFalse(summary["climate_mode"]["rollout"]["enable_allowed"])
        self.assertEqual(
            ["canary_room_not_selected"],
            summary["climate_mode"]["rollout"]["reasons"],
        )
        self.assertEqual("disabled", summary["readiness"]["status"])
        self.assertEqual(2, summary["registry"]["room_count"])
        bindings = summary["device_bindings"]
        self.assertEqual(2, bindings["device_count"])
        self.assertEqual(1, bindings["bound_count"])
        self.assertEqual(["sensor-living"], bindings["unbound_device_ids"])
        self.assertEqual(["sensor-living"], bindings["devices_without_candidates"])
        window = summary["shadow_window"]
        self.assertEqual(30, window["sample_count"])
        self.assertEqual(1, window["ready_room_count"])
        self.assertEqual(
            {"aligned": 1, "diverged": 1},
            summary["shadow_comparison"]["room_statuses"],
        )

    def test_formatted_summary_contains_no_entity_ids_or_token(self) -> None:
        rendered = format_summary(build_summary(_fake_results()))
        self.assertNotIn("entity_id", rendered)
        self.assertNotIn("climate.living_secret", rendered)
        self.assertNotIn("test-token", rendered)


class RunAuditTest(unittest.TestCase):
    def test_run_audit_fetches_all_endpoints_and_writes_raw_files(self) -> None:
        by_path = {
            result.path: result.payload for result in _fake_results()
        }

        def opener(request: object, timeout: float) -> _FakeResponse:
            url = getattr(request, "full_url")
            path = url.replace("http://ha.local:8123", "")
            return _FakeResponse(by_path[path])

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "audit"
            results, summary = run_audit(
                ACCESS, output_dir=output_dir, timeout=5.0, opener=opener
            )
            self.assertEqual(len(ENDPOINTS), len(results))
            for name, _ in ENDPOINTS:
                self.assertTrue((output_dir / f"{name}.json").is_file())
            self.assertTrue((output_dir / "summary.json").is_file())
            self.assertIn("climate_mode", summary)

    def test_tablet_only_403_is_recorded_and_audit_continues(self) -> None:
        by_path = {
            result.path: result.payload for result in _fake_results()
        }

        def opener(request: object, timeout: float) -> _FakeResponse:
            url = getattr(request, "full_url")
            path = url.replace("http://ha.local:8123", "")
            if path == "/api/hausman_hub/v1/capabilities":
                raise _http_error(403)
            return _FakeResponse(by_path[path])

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "audit"
            results, summary = run_audit(
                ACCESS, output_dir=output_dir, timeout=5.0, opener=opener
            )
        self.assertEqual(len(ENDPOINTS), len(results))
        capabilities = next(r for r in results if r.name == "capabilities")
        self.assertEqual(403, capabilities.status)
        self.assertIsNone(capabilities.payload)
        self.assertEqual(403, summary["endpoints"]["capabilities"])
        self.assertIn("climate_mode", summary)

    def test_core_endpoint_auth_failure_aborts_the_audit(self) -> None:
        by_path = {
            result.path: result.payload for result in _fake_results()
        }

        def opener(request: object, timeout: float) -> _FakeResponse:
            url = getattr(request, "full_url")
            path = url.replace("http://ha.local:8123", "")
            if path == "/api/config":
                raise _http_error(401)
            return _FakeResponse(by_path[path])

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "audit"
            with self.assertRaises(AuditAuthorizationError):
                run_audit(ACCESS, output_dir=output_dir, timeout=5.0, opener=opener)


if __name__ == "__main__":
    unittest.main()
