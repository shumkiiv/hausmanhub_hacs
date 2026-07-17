# Read-only integration skeleton

Created on 2026-07-13 after explicit owner approval for the safe skeleton.
The original private-first choice is preserved in [the historical skeleton
decision](read-only-skeleton-decision.md). The current public, manual-HACS
installation rule is recorded in [the HACS packaging
decision](hacs-packaging-decision.md).

The observation skeleton remains the base of HASC. Version 0.4.0 adds one
separate opt-in `input_boolean` canary described in the [control-canary
contract](canary-input-boolean-control.md). References below to exactly nine
entities and no control describe the default disarmed state.

## What it does

The `custom_components/hausman_hub/` package provides only a small Home
Assistant-facing shell around framework-independent safety rules:

- a single config entry with a selector for `read-only` or `shadow`;
- an options flow that can change only between those same two modes, close or
  restore the already-approved optional local page, and keep the established
  five-minute nine-count refresh or slow it to 15 or 30 minutes. It may also
  explicitly arm one canary for one selected `input_boolean`;
- a diagnostics snapshot assembled from a strict allow-list. Its entry summary
  contains only the validated effective HASC mode, optional-page boolean, and
  fixed five-, 15-, or 30-minute refresh choice; raw config-entry data and
  options are never copied;
- a fixed unavailable diagnostics response when HASC is not the one loaded
  setup. It contains no count and does not read the home;
- fixed manual guidance texts for review; they do not create issues or make
  changes.
- an original local brand icon for Home Assistant's interface; it is only an
  image and does not add a runtime capability.
- exactly nine diagnostic number sensors that show the already-approved
  aggregate summary. They share one local snapshot, do not count themselves,
  have no action, and expose no source name, identifier, reading, or history.
  Each has only a fixed ordinary visual icon, so the nine rows are easier to
  recognise without showing anything else about the home.
- when explicitly armed, one HASC switch that mirrors one selected
  `input_boolean` and calls only that helper's standard on/off services. It is
  absent by default, has no device attachment, and is removed with its saved
  target when the owner disarms it.
- one authenticated, local-network view at one fixed address for the
  already-approved nine-count summary. Only GET can return the nine counts;
  Home Assistant's service check is closed before any read. The view has no
  alternate address, including the same address with an extra trailing slash or
  added query data. It requires Home Assistant's exact built-in read-only group
  and accepts only loopback (`127.0.0.0/8` or `::1`), RFC 1918 IPv4
  (`10.0.0.0/8`, `172.16.0.0/12`, or `192.168.0.0/16`), or unique-local IPv6
  (`fc00::/7`). An IPv4 address written inside IPv6, including
  `::ffff:127.x.x.x`, follows the same IPv4 rule. It has no command method or
  outgoing connection. The owner may close this optional page in HASC's
  settings without changing the nine diagnostic numbers or diagnostics. A
  previously opened address then returns only that the summary is unavailable.

The inner `domain/` and `application/` layers use standard Python only. The
Home Assistant modules are thin adapters at the outer boundary.

## What it deliberately does not do

- It does not list or expose real areas or devices. The only selectable entity
  is the one local `input_boolean` canary target, stored in Home Assistant
  options and omitted from diagnostics.
- It does not use Node-RED, external APIs, or physical-device commands. Its
  only approved service calls are the canary helper's standard on/off actions.
- It does not create devices, buttons, `services.yaml`, repairs issues, or
  automatic fixes. Its default entities are the nine approved diagnostic count
  sensors; an armed canary adds exactly one HASC switch without a device.
- Its small `hacs.json` supports manual HACS installation from this public
  repository. It does not add the integration to the public HACS catalog or
  change its runtime behavior.
- `proxy` is absent and general physical-device execution remains
  `direct_execution_blocked`.

## Local verification

Run the existing local test suite:

```sh
python3 -m unittest discover -s tests -v
```

This checks the pure safety rules, manifest, translations, diagnostics
allow-list, and exact single-helper execution boundary. It does not load Home
Assistant or access a live home.

The suite also includes an in-memory adapter test for config and options flow.
It supplies only the small Home Assistant form API surface used by this package
and checks the safe paths and the rejected `proxy` path. It is not a Home
Assistant runtime test and does not claim runtime compatibility.

Home Assistant Core 2026.6.4 requires Python 3.14.2 or newer, while this local
project environment uses Python 3.12. A real Core compatibility run therefore
remains a separate task in an isolated Python 3.14 environment, still without
a live home or device access.

Use the explicit smoke check only from such an isolated environment:

```sh
uv venv --python 3.14 /tmp/hasc-core
uv pip install --python /tmp/hasc-core/bin/python homeassistant==2026.6.4
/tmp/hasc-core/bin/python tools/check_home_assistant_core.py
```

The script creates a temporary empty Home Assistant configuration, copies the
local integration into it, and removes the temporary configuration afterwards.
It checks both approved initial modes, a safe change between those modes, a
real reload, the redacted diagnostics report with only effective validated
HASC settings, clean removal, exactly nine HASC diagnostic count sensors, and
the absence of HASC services or devices in the default disarmed state. It then
creates one disposable `input_boolean`, arms the HASC canary, verifies the one
extra switch and its real local on/off calls, disarms it, and requires the
target and HASC switch to disappear without changing the helper. It also checks that a legacy entry
with empty options reports the safe page and five-minute defaults.
It also starts a temporary loopback-only
Home Assistant server to prove that the local nine-count page rejects an
unsigned request and an administrator, accepts only the temporary read-only
test account, and rejects every request method except GET before it can read
the summary or return the names of its nine counts. It then changes that
temporary account to the ordinary user group and proves that its existing local
token immediately loses access without reading the summary. A guest and an
administrator also receive no count names. Every response produced by HASC's
local page also asks the client not to store it, so a browser cannot retain an old
successful nine-count response. An attempt to submit `proxy` through options
is required to be rejected before it can persist anything. It does not read
any real Home Assistant configuration, credentials, entities, or devices.
It also turns the optional local page off and back on. When it is off, the
same nine sensors and redacted diagnostics remain, while an old authenticated
address returns only unavailable before that page request can read a summary.
The same approved counts may still refresh the nine retained sensors; turning
off the page deliberately does not turn off that display. A text value such as
`"false"` is rejected instead of being treated as an on/off choice.
The check also rejects a faster one-minute cadence, applies the exact 5, 15,
and 30 minute choices to the same shared coordinator, and proves that changing
that choice while HASC is stopped or user-disabled neither reloads HASC nor
reads the empty test home. The local GET page remains immediate per request.
The same empty check also tries the otherwise identical address with one extra
trailing slash and with added query data. Neither may reach HASC's one page,
read the summary, or return the nine count names.

The same empty check also makes the temporary local summary reader fail once.
The page must then return only its fixed unavailable response, with no count or
technical error detail.

The same empty test removes one safe HASC setup completely and then creates a
new one in the other safe mode. This confirms that removing and installing
HASC again does not leave an old HASC object, service, or setting behind.
Before changing the first safe setup, the test also tries to create a second
one. Home Assistant must refuse it, keep the first setup unchanged, and retain
exactly its nine count sensors.

Before that removal, the test saves one safe HASC setup, stops the empty test
system, replaces its local HASC copy, and starts the empty system again. The
same safe choice must be present after the restart, HASC must retain exactly
its nine diagnostic count sensors and no service, and direct execution must
still be blocked. This checks
the safe persistence path for an HASC update without touching a real home.

Before the user deactivation, the same empty test also temporarily stops one
safe, still user-enabled HASC setup and starts that exact saved setup again.
During the stop its nine sensor records must stay enabled and present, while
their temporary count values and local summary page become unavailable without
returning any counts. Starting the same setup again must restore only the same
nine count sensors, the fixed safe diagnostics report, and the authenticated
GET-only page. This is separate from user deactivation: it does not mark the
saved setup or its sensor records as disabled, and it does not touch a real
home.

That same temporary stop also simulates an old internal page reference that
would otherwise remain after HASC stops. The retained page must answer only
that the summary is unavailable before it reads any count. This is a
fail-closed check in the disposable empty configuration; it does not inspect a
real Home Assistant or home.

The empty test also repeats that ordinary stop and then fully stops its
temporary Home Assistant. A new empty Home Assistant must automatically load
the same still-enabled HASC setup. It must restore only the same nine count
sensors, fixed safe diagnostics, and authenticated GET-only page, while still
having no device, service, proxy, or home-control capability. This is separate
from user deactivation, which must remain inactive after a restart.

While that first safe setup is ordinarily stopped, the empty test also tries to
add HASC again. Home Assistant must refuse the second setup and keep exactly
one still-enabled saved setup. Its nine current values and guarded page must
stay unavailable, and no extra sensor, device, service, or control can appear.

Before that first empty system is stopped, the check also uses Home Assistant's
normal user deactivation control. The saved HASC setup remains, but all nine
count sensors are marked disabled, their temporary values disappear, and the
local summary page becomes unavailable without returning counts. Turning HASC
back on must restore the same nine enabled count sensors, the fixed safe
diagnostics report, and the authenticated GET-only page. It must not create a
device, service, or any control of the home.

The same empty check turns HASC off again immediately before replacing its
temporary HASC copy and restarting Home Assistant. The saved setup must remain
disabled: it cannot restore runtime data, count states, or the local page by
itself. Only an explicit activation after that restart may restore the same
nine count sensors, safe diagnostics, and authenticated GET-only page.

While that saved setup stays turned off after the temporary restart, the empty
check also tries to add HASC again. Home Assistant must refuse the second
setup, keep the single saved setup turned off, and leave its nine records,
values, and local page unavailable. Only the normal explicit activation may
restore the same nine safe counts.

The empty check also deliberately creates malformed saved pairs: one with two
enabled valid-looking entries, and one with a user-deactivated valid entry and
a user-enabled duplicate. The duplicate is inserted only through the
temporary Core test manager. If one HASC entry was already working, adding the
second immediately closes its nine-count display; the retained GET-only route
returns only an unavailable response. After a restart, HASC must leave both
saved records available for manual repair but load neither one. It must clear
the old nine records, their values, runtime data, services, and local page. It
never chooses, deletes, or activates either saved record. After the test
removes one record, an enabled remaining record must stay closed until an
explicit normal reload; a user-deactivated remaining record must stay closed
until an explicit normal activation. Only then may it restore exactly nine
count sensors, fixed diagnostics, and the authenticated GET-only page. These
malformed-pair tests run only in the disposable empty configuration and never
touch a real Home Assistant setup.

The same empty check asks for diagnostics while HASC is ordinarily stopped,
user-deactivated, removed, or part of either malformed pair. In every closed
case the report contains only the fixed unavailable status; a temporary guard
makes the check fail if diagnostics tries to read the home summary.

The empty check also reserves one HASC-like internal sensor name before a new
safe setup. HASC must still create all nine count sensors under distinct,
HASC-prefixed names. This protects a new installation from being blocked by a
name that was already in use, without reading a real home. After HASC is
removed, that same temporary external record must still exist unchanged. This
proves that cleanup removes only HASC's own records. The same empty system then
installs HASC again and requires the same nine count sensors while keeping the
external record unchanged. It deactivates that second setup, restarts the empty
system, and confirms that the saved setup stays disabled with no values or
local page. It then removes that preserved disabled setup and requires the
nine HASC records, temporary states, and local page to stay cleared through the
following restart while the external record remains unchanged. This confirms
that a user can remove a deactivated HASC setup without leaving its own data
behind.

The local nine-count page remains registered so a later safe setup can reuse
it without creating a duplicate. After each removal, however, an authenticated
temporary read-only user must receive only an unavailable response, never any
of the nine counts. The nine temporary count states must also be absent.
Whenever a safe setup is active, the empty check also requires exactly one
such page. This covers the repeated activation, deactivation, removal, and
reinstallation cycle, so the page cannot quietly accumulate copies.
The same empty check also saves five deliberately invalid main settings in
its temporary HASC setup, then restarts: an unsafe mode, a broken marker that
would otherwise claim execution is allowed, a setting with the required
execution block missing, a setting with the required mode missing even though
the separate safe choice says `shadow`, and an otherwise safe setting with one
extra synthetic field. HASC must refuse to load every case: no runtime data,
count state, stale HASC records, device, service, or local page may return.
Fast tests also reject further representative extra fields in either part of
the settings.
The same temporary check also proves that an explicit reload of each bad setup
closes the page immediately. These checks do not use a real Home Assistant
configuration.
Within that empty test only, each deliberately bad saved setting is then
corrected to the approved read-only setting and reloaded. HASC must restore
only its nine count sensors, safe diagnostics, and authenticated GET-only page;
the empty test system then restarts once while that corrected setup remains in
place. It must remember the correction and restore the same nine sensors before
the recovered setup is removed and checked through one more empty restart. This
is a test of safe recovery, not a request to edit a real Home Assistant file.
The same disposable check also saves two invalid values only in the separate
temporary mode-choice setting: an unsafe `proxy` value and an otherwise safe
`shadow` value with one extra synthetic field. Each must close just as
completely, remain closed through a restart, then recover only after the exact
original approved choice is restored. That corrected choice must also survive
its own empty restart before the setup is removed.
After the final removal, the empty test system starts once more. HASC must stay
absent there: no setup, sensor, device, service, count state, runtime data, or
local page may return, while the unrelated temporary external record remains
unchanged.
Only after that absence check, the same empty system installs HASC again in
`read-only` mode. The new setup must receive a new internal identifier, create
only the nine allowed count sensors, preserve the external record, retain the
safe diagnostics report, and restore the authenticated GET-only local page.
That fresh setup is removed too. Its local page must immediately become
unavailable without count data while the external record stays unchanged. A
fourth empty test-system start then requires HASC to remain completely absent.

For a manual check of an installed copy, see the Russian
[safe-check guide](home-assistant-safe-check.md). It asks only for Home
Assistant screens and explicitly excludes diagnostics archives and home data.
