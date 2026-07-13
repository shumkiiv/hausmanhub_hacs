"""Manual-only repairs boundary.

There is deliberately no issue creation, auto-fix, service invocation, deploy,
or device control in this module. It only exposes fixed guidance text for a
future user-visible review surface.
"""

from __future__ import annotations

from .application.repairs import ManualRepairGuidance, manual_guidance_for

__all__ = ["ManualRepairGuidance", "manual_guidance_for"]
