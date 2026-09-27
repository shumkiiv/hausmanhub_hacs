"""Closed transport of the lighting path (S-04/S-03, delivery HH-014.b).

The new lighting branch is physically unable to send: the transport has
no Home Assistant or MQTT access, no fallback and no delegate. Every
call returns an error whose outcome is proven as "not sent", so the core
records a terminal failure instead of an unknown dispatch.
"""

from __future__ import annotations

from typing import Mapping

from ...application.core.command_service import (
    PreparedCall,
    TransportOutcome,
    TransportPort,
    TransportStatus,
)

REASON_CLOSED_TRANSPORT = "closed_transport"


class ClosedTransport(TransportPort):
    """The branch runs with physical sending closed by design (R-01)."""

    def __init__(self, reason: str = REASON_CLOSED_TRANSPORT) -> None:
        self._reason = reason

    def call(self, prepared: PreparedCall) -> TransportOutcome:
        del prepared  # nothing is ever sent through this transport
        return TransportOutcome(
            status=TransportStatus.ERROR,
            error_detail=self._reason,
            # Proven non-send: this transport cannot reach any device.
            proven_not_sent=True,
        )
