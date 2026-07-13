# Engineering and review decision

Date: 2026-07-13.

## User decision

HASC must be designed and implemented according to Clean Code and Clean
Architecture. Kimi code review is mandatory for every future code change.

## Operational rule

Before a code change is reported complete or pushed, provide Kimi with the
final diff, intended scope, applicable architecture and safety boundaries, and
local test results. Address its findings or explicitly document the reason for
not applying a finding. The final report must state the Kimi review result.

This review rule is additive: it cannot authorize proxy, direct execution,
runtime access, device commands, Node-RED deployment, or ownership transfer.
