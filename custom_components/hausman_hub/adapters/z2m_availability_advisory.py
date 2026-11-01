"""Offer enabling Zigbee2MQTT availability checks and state retention.

Read-only advisory: the adapter subscribes to the retained
``zigbee2mqtt/bridge/info`` bridge status through the same Home Assistant
MQTT client the lighting observations already use, inspects exactly two
fields — ``config.availability.enabled`` and ``config.device_options.retain``
— and manages this integration's own two repairs issues:

availability:
- exactly ``false`` -> create issue ``z2m_availability_disabled``;
- exactly ``true``  -> delete the issue;
- anything unknown -> do nothing.

state retention:
- exactly ``false``, or the key missing inside a well-formed config
  -> create issue ``z2m_state_retain_disabled``;
- exactly ``true``  -> delete the issue;
- anything unknown -> do nothing.

The advisory never sends device commands, never writes configuration, never
publishes to any topic, and its failure must not affect the lighting runtime
in any way. The two hints are decided independently on the same message and
isolate each other's failures. Stopping or unloading the integration cancels
the subscription but deliberately keeps already-created issues visible.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import Any, Callable

_LOGGER = logging.getLogger(__name__)

TOPIC = "zigbee2mqtt/bridge/info"
ISSUE_ID = "z2m_availability_disabled"
RETAIN_ISSUE_ID = "z2m_state_retain_disabled"
MAX_PAYLOAD_SIZE = 65536
SUBSCRIBE_TIMEOUT_SECONDS = 5
RETRY_DELAY_SECONDS = 5

_DECIDE_CREATE = "create"
_DECIDE_DELETE = "delete"


def _decode_bridge_config(payload: Any) -> dict[str, Any] | None:
    """Decode one bridge/info payload into its ``config`` object.

    Only ``str``, ``bytes`` and ``bytearray`` payloads are examined at all;
    the size limit is enforced before any decoding or parsing so hostile
    input cannot cost unbounded memory, and excessive nesting is rejected
    by catching ``RecursionError`` raised during ``json.loads``. Anything
    unknown returns ``None``: a hint must never be based on a guess. The
    full payload is never logged.
    """
    if isinstance(payload, (bytes, bytearray)):
        if len(payload) > MAX_PAYLOAD_SIZE:
            return None
        try:
            payload = bytes(payload).decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            return None
    if not isinstance(payload, str):
        return None
    if len(payload) > MAX_PAYLOAD_SIZE:
        return None
    try:
        data = json.loads(payload)
    except (ValueError, TypeError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    config = data.get("config")
    if not isinstance(config, dict):
        return None
    return config


def decide_availability_advice(payload: Any) -> str | None:
    """Map one bridge/info payload to an advice decision without guessing.

    ``"create"`` when availability is exactly disabled, ``"delete"`` when it
    is exactly enabled, ``None`` for anything unknown.
    """
    config = _decode_bridge_config(payload)
    if config is None:
        return None
    availability = config.get("availability")
    if not isinstance(availability, dict):
        return None
    enabled = availability.get("enabled")
    if enabled is False:
        return _DECIDE_CREATE
    if enabled is True:
        return _DECIDE_DELETE
    return None


def decide_state_retain_advice(payload: Any) -> str | None:
    """Map one bridge/info payload to a state-retention advice decision.

    ``"create"`` when the ``retain`` key is missing (either no
    ``device_options`` key at all inside a well-formed ``config``, or no
    ``retain`` key inside a dict ``device_options``) — the Zigbee2MQTT
    default is no retention; ``"delete"`` when it is exactly ``True``,
    ``"create"`` when exactly ``False``. A ``device_options`` value that is
    present but not a dict is damaged input and returns ``None``, as does
    a missing/malformed ``config``, a non-boolean ``retain``, a broken or
    oversized message: a hint must never be based on damaged input.
    """
    config = _decode_bridge_config(payload)
    if config is None:
        return None
    if "device_options" not in config:
        return _DECIDE_CREATE
    device_options = config["device_options"]
    if not isinstance(device_options, dict):
        return None
    if "retain" not in device_options:
        return _DECIDE_CREATE
    retain = device_options["retain"]
    if retain is False:
        return _DECIDE_CREATE
    if retain is True:
        return _DECIDE_DELETE
    return None


def _mqtt_ready(ready: Any) -> bool:
    """Only an explicit ``False`` means the MQTT client never became ready.

    ``async_wait_for_mqtt_client`` signals success by returning ``None``
    (or ``True``); treating ``None`` as "not ready" would silently disable
    the whole advisory under real Home Assistant.
    """
    return ready is not False


async def _subscribe_with_limits(
    ha_mqtt: Any,
    hass_: Any,
    topic: str,
    callback: Callable[[Any], None],
    timeout_seconds: int = SUBSCRIBE_TIMEOUT_SECONDS,
):
    """Wait for the MQTT client and subscribe inside one bounded flow.

    This is the single real connection path used by the production wiring;
    tests drive it with a fake transport instead of a second algorithm.
    A transport failure or timeout yields no subscription at all — and the
    callers of the lighting runtime are never delayed by more than the
    timeout bound.
    """
    try:
        async with asyncio.timeout(timeout_seconds):
            ready = await ha_mqtt.async_wait_for_mqtt_client(hass_)
            if not _mqtt_ready(ready):
                return None
            return await ha_mqtt.async_subscribe(hass_, topic, callback, 0)
    except Exception:  # noqa: BLE001 - transport failure -> no subscription
        return None


class Z2mAvailabilityAdvisory:
    """Subscribe once, decide per message, manage exactly one own issue."""

    def __init__(
        self,
        hass: Any,
        *,
        subscribe: Callable[..., Any],
        create_issue: Callable[..., Any],
        delete_issue: Callable[..., Any],
        schedule_retry: Callable[..., Any] | None = None,
        create_retain_issue: Callable[..., Any] | None = None,
        delete_retain_issue: Callable[..., Any] | None = None,
    ) -> None:
        self._hass = hass
        self._subscribe = subscribe
        self._create_issue = create_issue
        self._delete_issue = delete_issue
        self._create_retain_issue = create_retain_issue
        self._delete_retain_issue = delete_retain_issue
        self._schedule_retry = schedule_retry
        self._unsub: Callable[[], Any] | None = None
        self._closed = False
        self._started = False
        self._attempt_in_flight = False
        self._retry_cancel: Callable[[], Any] | None = None

    async def start(self) -> None:
        """Subscribe once; a closed advisory never starts subscribing."""
        if self._closed or self._started:
            return
        self._started = True
        await self._run_attempt(schedule_on_failure=True)

    async def _run_attempt(self, schedule_on_failure: bool) -> None:
        """One bounded subscription attempt, plus at most one delayed retry.

        The retry (if a scheduler port is wired at the Home Assistant edge)
        never blocks the caller: the first failed attempt schedules a timer
        and returns immediately. A task queued right before ``close()`` does
        nothing when it finally runs.
        """
        if self._closed:
            return
        if self._attempt_in_flight:
            return
        self._attempt_in_flight = True
        try:
            unsub = await self._subscribe(
                self._hass, TOPIC, self._handle_message,
            )
        except Exception:  # noqa: BLE001 - the hint must never raise
            _LOGGER.debug("z2m availability advisory subscription failed")
            unsub = None
        finally:
            self._attempt_in_flight = False
        if self._closed:
            # Closed while the subscription was being created: cancel it
            # immediately and keep nothing.
            if unsub is not None:
                self._cancel_pending_unsub(unsub)
            return
        if unsub is None:
            if (
                schedule_on_failure
                and self._schedule_retry is not None
                and self._retry_cancel is None
            ):
                try:
                    self._retry_cancel = self._schedule_retry(self._fire_retry)
                except Exception:  # noqa: BLE001
                    _LOGGER.debug(
                        "z2m availability advisory retry scheduling failed"
                    )
            return
        self._unsub = unsub

    def _fire_retry(self, _now: Any = None) -> None:
        """Sync retry action scheduled by the Home Assistant edge.

        Runs in the HA event loop and schedules exactly one more bounded
        attempt; it never blocks the loop and never schedules a second retry.
        """
        if self._closed or self._unsub is not None or self._retry_cancel is None:
            return
        self._retry_cancel = None
        if self._attempt_in_flight:
            return
        schedule = getattr(self._hass, "async_create_task", None)
        if callable(schedule):
            schedule(self._run_attempt(schedule_on_failure=False))

    def _handle_message(self, message: Any) -> None:
        if self._closed:
            return
        payload = getattr(message, "payload", None)
        try:
            decision = decide_availability_advice(payload)
            if decision == _DECIDE_CREATE:
                self._create_issue(self._hass)
            elif decision == _DECIDE_DELETE:
                self._delete_issue(self._hass)
        except Exception:  # noqa: BLE001 - a hint must never raise
            _LOGGER.debug("z2m availability advisory message handling failed")
        # The retention hint is evaluated on the same message but with its
        # own isolated decision and ports: a failure of one hint must not
        # suppress the other.
        if self._create_retain_issue is None or self._delete_retain_issue is None:
            return
        try:
            decision = decide_state_retain_advice(payload)
            if decision == _DECIDE_CREATE:
                self._create_retain_issue(self._hass)
            elif decision == _DECIDE_DELETE:
                self._delete_retain_issue(self._hass)
        except Exception:  # noqa: BLE001 - a hint must never raise
            _LOGGER.debug("z2m state retain advisory message handling failed")

    def _cancel_pending_unsub(self, unsub: Callable[[], Any]) -> None:
        """Cancel a subscription that was never stored (late-close case).

        An awaitable unsubscribe result is scheduled on the loop when the
        hass object supports it, mirroring the close() behavior.
        """
        try:
            result = unsub()
            if inspect.isawaitable(result) and self._hass is not None:
                schedule = getattr(self._hass, "async_create_task", None)
                if callable(schedule):
                    schedule(result)
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "z2m availability advisory late unsubscribe failed"
            )

    def close(self) -> None:
        """Synchronous close for HA unload callbacks.

        The pending retry timer and the MQTT subscription are cleaned up
        independently: a failing retry-cancel never blocks the unsubscribe.
        The unsubscribe is attempted exactly once; a failing unsubscribe
        must not raise and must not be retried. Messages are still ignored
        afterwards via the closed flag. An existing repairs issue is never
        deleted here — only a bridge message with ``enabled=true`` removes
        it.
        """
        self._closed = True
        cancel = self._retry_cancel
        self._retry_cancel = None
        if cancel is not None:
            try:
                cancel()
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "z2m availability advisory retry cancel failed"
                )
        unsub = self._unsub
        try:
            if unsub is not None:
                result = unsub()
                if inspect.isawaitable(result) and self._hass is not None:
                    schedule = getattr(self._hass, "async_create_task", None)
                    if callable(schedule):
                        schedule(result)
        except Exception:  # noqa: BLE001 - shutdown must never raise
            _LOGGER.debug("z2m availability advisory unsubscribe failed")
        finally:
            self._unsub = None

    async def async_close(self) -> None:
        """Async-compatible close for existing callers."""
        self.close()


class _NoopAdvisory:
    """Failure replacement: no subscription, no issue, nothing to close."""

    def close(self) -> None:
        return None

    async def async_close(self) -> None:
        return None


async def start_z2m_availability_advisory(hass: Any) -> Any:
    """Wire the advisory to the real Home Assistant MQTT and repairs ports.

    Any missing dependency (no MQTT integration, no helpers, API drift)
    degrades to the no-op advisory: an unavailable transport must not create
    a hint and must not break integration setup.
    """
    try:
        from homeassistant.components import mqtt as ha_mqtt
        from homeassistant.core import callback as ha_core_callback
        from homeassistant.helpers.event import async_call_later
        from homeassistant.helpers.issue_registry import (
            IssueSeverity,
            async_create_issue,
            async_delete_issue,
        )

        def subscribe(hass_: Any, topic: str, message_callback:
                      Callable[[Any], None]):
            # Real HA MQTT dispatches plain callables to executor threads,
            # where the issue registry calls are unsafe: pin the handler to
            # the event loop with the HA callback decorator (same pattern as
            # the lighting MQTT observations adapter).
            def receive(message: Any) -> None:
                message_callback(message)

            return _subscribe_with_limits(
                ha_mqtt, hass_, topic, ha_core_callback(receive),
            )

        def schedule_retry(action: Callable[..., Any]) -> Callable[[], Any]:
            # One delayed retry if the MQTT client was not ready yet. The
            # timer never blocks integration setup: async_call_later returns
            # immediately and the action itself runs on the loop. The HA
            # callback decorator only works on plain functions, so the
            # bound adapter method is called through a plain wrapper.
            def fire(now: Any) -> None:
                action(now)

            return async_call_later(
                hass, RETRY_DELAY_SECONDS, ha_core_callback(fire),
            )

        def create_issue_port(hass_: Any) -> None:
            async_create_issue(
                hass_,
                "hausman_hub",
                ISSUE_ID,
                is_fixable=False,
                severity=IssueSeverity.WARNING,
                translation_key=ISSUE_ID,
                translation_placeholders={},
            )

        def delete_issue_port(hass_: Any) -> None:
            async_delete_issue(hass_, "hausman_hub", ISSUE_ID)

        def create_retain_issue_port(hass_: Any) -> None:
            async_create_issue(
                hass_,
                "hausman_hub",
                RETAIN_ISSUE_ID,
                is_fixable=False,
                severity=IssueSeverity.WARNING,
                translation_key=RETAIN_ISSUE_ID,
                translation_placeholders={},
            )

        def delete_retain_issue_port(hass_: Any) -> None:
            async_delete_issue(hass_, "hausman_hub", RETAIN_ISSUE_ID)

    except Exception:  # noqa: BLE001 - no MQTT/helpers -> no hint
        return _NoopAdvisory()

    advisory = Z2mAvailabilityAdvisory(
        hass, subscribe=subscribe, create_issue=create_issue_port,
        delete_issue=delete_issue_port, schedule_retry=schedule_retry,
        create_retain_issue=create_retain_issue_port,
        delete_retain_issue=delete_retain_issue_port,
    )
    try:
        await advisory.start()
    except Exception:  # noqa: BLE001 - setup must not fail because of the hint
        return _NoopAdvisory()
    return advisory
