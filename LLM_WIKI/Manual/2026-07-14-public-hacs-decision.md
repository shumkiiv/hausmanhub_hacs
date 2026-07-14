# Public custom-HACS decision

Date: 2026-07-14.

## Owner decision

After HACS returned GitHub 404 for the private repository, the owner explicitly
approved making `shumkiiv/hausmanhub_hasc` public. HACS does not support
private GitHub repositories. The repository is not submitted to the public
HACS catalog.

## Allowed use

- Add the public GitHub URL manually in HACS as an **Integration** custom
  repository.
- Keep the existing minimal `hacs.json` and public installation instructions.

## Still prohibited

- Public HACS catalog listing or any broader release decision.
- A live connection to Home Assistant, Node-RED, devices, or external APIs by
  this repository's development work.
- Proxy, direct execution, secrets, live identifiers, service paths, command
  payloads, deployment scripts, or authority over another contour.
