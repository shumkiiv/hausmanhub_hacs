# Decision: guarded local access to the HASC nine-count summary

Date: 2026-07-14.

## Owner approval

The owner explicitly approved local HASC access only to the fixed nine-count
home summary. The approval does not cover names, identifiers, readings,
history, commands, services, proxy, direct execution, remote internet access,
or authority over any contour.

## Approved boundary

Version 0.2.0 may add one inbound Home Assistant view with all of these
conditions:

- authenticated GET only; no POST, PUT, PATCH, or DELETE handler;
- a local or loopback network origin only;
- exactly Home Assistant's built-in `system-read-only` group, with no admin,
  system-generated, or mixed-group user accepted;
- exact same nine aggregate counts as diagnostics, with configuration checked
  again before each response;
- no token, password, URL, live identifier, state value, history, or command
  persisted in the repository or integration configuration;
- no outbound connection, service call, entity, automation, repair, proxy, or
  direct execution.

The view fails closed when a condition is absent, the entry is unloaded, or
the stored safe configuration is invalid.

## Activation gate

The owner must update HASC first, then create a separate non-administrator
Home Assistant account with only the built-in read-only group. If the current
Home Assistant interface does not offer that exact group, do not edit internal
storage manually. Stop at that point and select another safe approach through
a new decision.

Any credential remains local to the owner and outside the HASC repository.
No live API call is made while implementing or verifying this change.
