---
name: samsung-tv-connect
description: Connect an explicitly authorized Samsung TV with installed SDB and diagnose Developer Mode, Mac privacy or an existing approved relay. Not for creating security bypasses or installing SDKs.
---

# Connect the named TV, not a guessed endpoint

Resolve the toolkit at `../..` from this skill's reported base directory.
Read [Common contract](../common.md) for permission, ownership and host limits,
then [prerequisites](../../docs/prerequisites.md).

1. For a new TV, follow [new-TV onboarding](../../docs/getting-started.md#new-tv-onboarding):
   bounded discovery only in the granted network scope while no sibling lane is
   active; confirm candidates, leave unreturned metadata unknown, then configure
   Developer Mode/Host PC IP and the new target's certificate/install permission.
   Check installed SDB version/path without contacting a TV. Record host, SDK
   and model/firmware privately. Confirm Developer Mode's host IP is the
   interface actually used by the computer; follow Samsung's model instructions.
2. Obtain explicit connection authorization and exclusive physical-TV ownership.
   Use `tv connect` from the [getting-started guide](../../docs/getting-started.md) or Device Manager.
   Require the exact selected serial in `sdb devices` with state `device`;
   reachability or a successful TCP socket alone is insufficient.
3. On macOS, a working Apple `nc` probe and failed SDB connection can indicate
   the launching application's Local Network permission, not bad TV firmware.
   Fix that application's normal permission, route and firewall configuration.
   Do not kill a shared SDB server to change its parent application.
4. An existing relay is usable only when its owner approved the endpoint,
   target, lifetime and teardown and policy permits it. Select the actual
   loopback serial shown by SDB consistently for subsequent SDK commands.
   A relay is a connection route, not an inspector forward.
5. Never create a relay to evade Local Network privacy or another security
   control. The upstream `nc` workaround is historical evidence, not a toolkit
   command or permission grant. No relay helper is shipped here.

Verify connection against the intended Device Manager target privately; do not
publish `sdb devices` output. Stop on ambiguous targets, refused permission,
another lane or uncertain ownership. Report the blocked cause rather than
rebooting, disconnecting everyone or claiming an install.

Evidence: [upstream setup observations](https://github.com/dragoshont/plx-native/blob/dragoshont-samsung-tizen-feasibility/docs/samsung-getting-started.md).
