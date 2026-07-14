# Superseded private HACS approval

Date: 2026-07-14.

## Owner decision

The owner initially chose option 2 from `docs/hacs-packaging-decision.md`:
allow HACS installation only for the owner through the private GitHub
repository.

This decision was superseded on the same day. HACS does not support private
GitHub repositories, so the owner explicitly approved making the repository
public for manual HACS custom-repository installation. The public change is
recorded in `docs/hacs-packaging-decision.md`.

## Allowed implementation

- Add the minimal root `hacs.json` with the display name and the Home
  Assistant Core 2026.7.0 baseline.
- Add installation instructions and a local test that fixes the exact
  metadata shape.
- Keep the HACS change small and do not add a public HACS catalog listing.

## Still prohibited

- Public HACS catalog listing or any public release decision.
- This repository change does not install or test against a live Home
  Assistant, Node-RED, device, or external API.
- Credentials, live identifiers, service paths, command payloads, deployment,
  proxy, or direct execution.

The metadata change must receive Kimi review, local tests, staged-file safety
inspection, a dedicated commit, and push before it is complete.
