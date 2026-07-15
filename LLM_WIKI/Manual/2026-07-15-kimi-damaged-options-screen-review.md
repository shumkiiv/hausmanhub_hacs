# Kimi review: damaged options screen

Date: 2026-07-15.

## Scope

Independent read-only review of the upcoming HASC 0.3.11 change. The change
makes the options screen choose `read-only` when any saved main setting or
mode option is damaged. Opening the screen must leave the saved settings
unchanged so repair remains a manual Home Assistant action.

## Result

Kimi session `ses_0989ad4f3ffewWcxxbNIIoJop5` returned **NO FINDINGS**.

The reviewer confirmed that the form now validates the complete saved
configuration before selecting its default, that valid `shadow` remains
available, and that no read path, service, device, command, proxy, or direct
execution capability was added.

## Evidence

- 111 local synthetic and boundary checks passed.
- The disposable empty Home Assistant Core checks passed with 2026.6.4 and
  2026.7.0.
- The Core check opens the damaged options form for every existing damaged
  main-setting and option variant, requires `read-only` as its default, then
  confirms that opening and closing the form did not change either saved
  mapping.

All checks use only the repository, synthetic fixtures, and temporary empty
Home Assistant configurations. They do not connect to a real home, Node-RED,
devices, services, or remote Home Assistant API.
