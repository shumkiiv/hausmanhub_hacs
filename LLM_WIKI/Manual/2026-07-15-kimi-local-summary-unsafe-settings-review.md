# Kimi review: local summary after unsafe saved settings

Date: 2026-07-15.

## Scope

Version 0.3.9 changes the local nine-count page so that the application layer
checks the saved HASC data and mode choice before it asks the outer adapter to
read the local home summary. If those saved values become unsafe while HASC is
still loaded and before an explicit reload, the page must return only its
unavailable response and must not read the home.

The disposable Core lifecycle covers five invalid main-settings variants and
two invalid mode-choice variants. It confirms that the entry is still loaded,
replaces the page's local summary reader with a function that fails if called,
then requires an authenticated local GET request to return 503 without count
keys.

## Final result

Kimi session `ses_098c407b4ffebLPyCUltCyZdlv` (model `k2p7`) returned
`NO FINDINGS` after the final review, including the explicit loaded-entry
assertion in the disposable Core helper and the version-package test update.

The review confirmed that the safe path still exposes only the fixed nine
counts, the unsafe path reads none, the temporary reader replacement is
restored, and the change adds no device, service, proxy, direct execution, or
other home-control authority.

## Verification

- `python3 -m unittest discover -s tests -v` — 107 passed.
- `tools/check_home_assistant_core.py` — passed in isolated Core 2026.6.4 and
  2026.7.0 environments.

Every check used disposable empty configurations or synthetic data. No real
Home Assistant, Node-RED, device, credential, or home data was accessed.
