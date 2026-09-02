"""Framework-independent errors for the climate operation ledger."""

from __future__ import annotations


class ClimateOperationRevisionConflict(ValueError):
    """A verified compare-and-swap conflict, not a storage outage."""
