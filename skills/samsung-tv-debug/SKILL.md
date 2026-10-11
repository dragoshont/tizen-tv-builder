---
name: samsung-tv-debug
description: Stop an authorized installed Samsung app before debug launch, discover its fresh inspector port and manage an owned loopback forward. Not a general TV shell or reboot workflow.
---

# Kill before debug; rediscover every launch

Resolve the toolkit at `../..` from this skill's reported base directory.
Read [Common contract](../common.md) for permission and lease requirements.
Use [debug commands](../../docs/getting-started.md#debug-and-private-viewer).

1. Confirm the installed `tizen:application` ID from this app's config.xml and
   authorize stop, debug and forward separately from read-only discovery.
   Use the exact selected serial, not the first device in a list.
2. Stop the **package prefix** (the ten-character portion before the dot), then
   debug the full application ID. This is a restricted Samsung launch command,
   not evidence of general shell access.
3. Read the port returned by this launch. Never reuse a port from documentation
   or a previous app session. If stop/debug times out, device state is uncertain:
   inspect in Device Manager before another bounded attempt.
4. On the upstream 2017/Tizen 3.0 set, `shell 0 debug APP` returned `closed`;
   explicit trailing `0` launched debug with a timeout warning. Opt in only for
   a relevant legacy target. It is not a fix for signing or every failure.
5. Inspect current forwards; reserve an unused local port and forward to the
   returned remote port under the lane. Verify the exact serial/local/remote
   mapping, then inspect `/json/version` and `/json/list` on loopback.
6. Close competing inspectors with their owners' cooperation if discovery is
   empty; do not kill their processes. Rediscover the page after every relaunch.
7. Teardown removes only your exact forward if the mapping still matches.
   Preserve pre-existing/changed mappings and report cleanup failure.

The workbench performs package-stop → debug → owned-forward → fresh discovery.
The POSIX `tizen.py debug` wrapper **does not stop first**; callers must do it
explicitly. HTTP targets establish discovery only, not a functioning WebSocket,
pixels, remote parity or app acceptance.

Evidence: [inspector study](https://github.com/dragoshont/plx-native/blob/dragoshont-samsung-tizen-feasibility/docs/samsung-web-inspector.md),
[dated legacy observation](../../docs/lessons.md).
