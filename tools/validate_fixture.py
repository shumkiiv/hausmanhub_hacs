#!/usr/bin/env python3
"""Validate one synthetic HASC fixture without accessing any runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hasc_validation import (
    validate_common_inventory,
    validate_diagnostics_contract,
    validate_shadow_evidence,
)


VALIDATORS = {
    "common": validate_common_inventory,
    "shadow": validate_shadow_evidence,
    "diagnostics": validate_diagnostics_contract,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=sorted(VALIDATORS))
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.fixture.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"invalid fixture input: {exc}", file=sys.stderr)
        return 2
    errors = VALIDATORS[args.kind](data)
    if errors:
        print(f"{args.fixture}: invalid", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"{args.fixture}: valid {args.kind} fixture")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
