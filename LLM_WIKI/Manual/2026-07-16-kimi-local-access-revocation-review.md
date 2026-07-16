# Kimi review: local access revocation

Date: 2026-07-16.

## Scope

The disposable Home Assistant Core check now proves that a local temporary
reader loses access to HASC's nine-count page as soon as the reader is moved
from Home Assistant's exact read-only group to the ordinary user group.

The existing token must return `403`, contain none of the nine count keys, and
must not trigger a home-summary read. The check uses only a loopback server in
an empty temporary Core configuration.

## Review

Kimi session `ses_0978222a7ffeLnwysYEcDev16E` using
`kimi-for-coding/k2p7` reviewed the final diff and returned **NO FINDINGS**.
It confirmed that:

- the same old token is checked after the group change;
- the exact read-only boundary still rejects the ordinary group;
- a blocking temporary reader proves the rejected request cannot read the
  summary;
- the error response cannot contain any of the nine counts;
- the helper split keeps existing temporary-token callers unchanged;
- the documentation accurately describes the check; and
- no live Home Assistant, device, service, Node-RED, secret, proxy, or
  execution capability is involved.

## Local evidence

- `python3 -m py_compile tools/check_home_assistant_core.py tests/test_read_only_skeleton.py`
- `python3 -m unittest discover -s tests -v` — 117 tests passed.
- Temporary empty Home Assistant Core 2026.6.4 and 2026.7.0 checks passed.

No real Home Assistant, device, Node-RED, service, or remote API was used.
