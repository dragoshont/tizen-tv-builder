---
name: samsung-tv-new-app
description: Start a minimal account-free Samsung Web app, validate/build/sign it and verify an authorized install using Mac or Windows SDK commands. Not a media-client template or blanket model certification.
---

# Minimal source to a signed developer app

Resolve the toolkit at `../..` from this skill's reported base directory.
Read [Common contract](../common.md) for permission and host limits,
[prerequisites](../../docs/prerequisites.md) and
[zero-to-signed-app commands](../../docs/getting-started.md).

1. Copy [hello-tv](../../examples/hello-tv/index.html) and
   [its manifest](../../examples/hello-tv/config.xml) into a new, owner-selected
   app directory. No SDK template redistribution, accounts or server needed.
2. Choose a unique ten-alphanumeric package ID and matching `PACKAGE.Application`.
   Set a deliberate minimum `required_version`, content entry and TV profile.
   The bundled `2.3` value is a manifest floor, not proof of 2015 support.
3. Keep ES5 output for the old-engine target (Tizen 3/M47, Tizen 4/M56).
   Probe runtime APIs separately; modern syntax parsing and desktop rendering
   cannot certify fetch, streams, decoders, graphics or TV input.
4. Run manifest validation and host tests, then unsigned source build. On
   Windows do not launch the POSIX wrappers; use installed `tizen.bat` directly.
5. Invoke [certificate workflow](../samsung-tv-certificates/SKILL.md). Sign
   with the existing named Samsung TV profile and use the newly emitted widget,
   not an old filename guessed from the app name.
6. Verify widget entries/signature-file presence and checksum privately.
   File presence is structural only, not cryptographic signature verification.
7. With explicit install/launch grants and a lease, install to the exact target,
   launch the full app ID and **observe** the hello text, focused Refresh button
   and runtime facts. Check Back/lifecycle on the physical remote separately.
8. Record source/package hashes, host/SDK/model/firmware and pass/fail/skip.
   Reuse IDs/author for updates; removal requires another explicit grant.

Add privileges only for implemented features. ProductInfo needs Samsung's
productinfo privilege and WebAPI script; extra remote keys need tv.inputdevice
registration. The account-free hello example intentionally needs neither.
Missing privilege `SecurityError` is not proof the hardware lacks an API.
See [runtime/input lessons](../../docs/lessons.md).
