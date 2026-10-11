# Samsung workflow contract

Resolve the toolkit root as `../..` from the invoked skill's host-reported base
directory, not from the application's working directory. Read the toolkit's
README, [compatibility](../docs/compatibility.md) and [lessons](../docs/lessons.md).
If the host supplies no base path, obtain the installed path through its skill
inventory; never guess a plugin cache. Keep the application's own policy.

Loading a skill grants no permission. No SDK installation, account sign-in,
certificate modification, relay, TV connection, stop/debug, install, pixels or
input without the corresponding explicit owner authorization. No root, firmware
changes, general retail shell, TLS/privacy/security bypass or household account
writes. Keep addresses, serials, DUIDs, certificates, passwords, browser tokens
and captures private. Public commands use placeholders.

Build first, then acquire the existing canonical Samsung lane for one bounded
device run. Do not invent a parallel lock or break another owner's lease.
The workbench owns short setup leases and bounded preview leases: do not nest
an external lock around those actions. Across hosts, coordinate physical-TV
ownership separately; a host-local lock is not a distributed lease.
Stop/reconcile before releasing; clean up only identified owned forwards and
client processes. Never kill a shared SDB server or remove all forwards.

Use Python 3.10+ standard library. SDK wrappers in `tools/tizen.py` are POSIX-only;
on Windows use Samsung CLI directly. `tools/workbench.py` has an independent
Windows SDB path but Windows execution/device acceptance is untested. It is an
unreleased candidate, not included in the published 0.1.0 experience.

Return source SHA, host/SDK version, commands and actual outcomes, exact private
target identity and authorized scope, lease/forward cleanup, pass/fail/skip and
unresolved limits. A package request, HTTP discovery, mock, emulator or desktop
pass is not cryptographic verification, retail-TV acceptance or Store approval.

Workflow details: [prerequisites](../docs/prerequisites.md),
[zero to signed app](../docs/getting-started.md),
[workbench](../docs/workbench.md). Lessons are independently expressed from the
[PlxNative study](https://github.com/dragoshont/plx-native); no GPL code is copied.
