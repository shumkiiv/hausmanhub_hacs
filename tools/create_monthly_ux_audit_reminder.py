#!/usr/bin/env python3
"""Write a local, identity-free reminder for the monthly UX audit."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path


OUTPUT_DIR = Path.home() / ".config" / "hausmanhub" / "operations"


def reminder(now: datetime | None = None) -> dict[str, str]:
    current = now or datetime.now(timezone.utc)
    return {
        "schema": "hausman-monthly-ux-audit-v1",
        "month": current.strftime("%Y-%m"),
        "created_at": current.isoformat(),
        "status": "due",
        "checklist": "docs/OPERATIONS_SUPPORT_SLO_DOD.md#ежемесячный-ux-аудит",
    }


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUTPUT_DIR / "monthly-ux-audit-due.json"
    temporary = OUTPUT_DIR / ".monthly-ux-audit-due.json.tmp"
    temporary.write_text(json.dumps(reminder(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(target)
    print("Hausman monthly UX audit is due")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
