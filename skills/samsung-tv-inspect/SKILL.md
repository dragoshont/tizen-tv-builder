---
name: samsung-tv-inspect
description: Inspect a Samsung Web app, open its private workbench viewer and use audited remote input with truthful renderer-paint provenance. Not a panel recorder, video-plane capture or system remote.
---

# Inspect, view and drive within the grant

Resolve the toolkit at `../..` from this skill's reported base directory.
Read [Common contract](../common.md) for permission and privacy holds,
[workbench](../../docs/workbench.md) for controls and
[live canvas](../../docs/live-canvas.md) if using Copilot's adapter.

1. Complete [debug setup](../samsung-tv-debug/SKILL.md). Choose a page from fresh
   discovery. The full Samsung Web Inspector is for DOM/CSS/console/network
   debugging; toolkit `inspect` is HTTP metadata only.
2. With the unreleased checkout, launch `python3 tools/workbench.py` on macOS or
   `py -3 tools/workbench.py` on Windows. Supply the installed SDB path if needed.
   Treat the local URL fragment as a bearer credential; keep it private.
3. Authorize pixels and input independently; audit even D-pad/Back handlers.
   Leave safe OK IDs empty until their current handlers are verified non-write.
   Do not navigate real household accounts or start media for UI tests.
4. Connect the chosen page, Refresh or Start a bounded feed, then observe actual
   frames. D-pad, Enter/Space and Backspace are browser controls. The Samsung
   button inventory includes optional/system keys, but unavailable keys stay
   dimmed. Extra app actions require consent and supported codes from
   `tvinputdevice.getSupportedKeys()`; preserve default keys. Never promise
   Samsung system-key equivalence from injected DOM events.
5. On Chrome 47, use legacy-compatible uncompressed WebSocket transport.
   Unsupported methods, no callback, no layers and missing frames remain
   unavailable/failures; no DOM reconstruction or synthetic success fallback.
6. Label `LayerTree.makeSnapshot` / `replaySnapshot` output **renderer-paint**.
   Layers may be missing/sequential; no atomic composition, AVPlay plane, TV
   menus, HDR/audio or panel recording is proven.
7. Pause, disconnect and release only owned forwards/leases. A private artifact
   must retain source, timestamp, target and actual capture outcome.

If recording is exposed, check detected encoder support before connecting,
pause the preview feed, and choose a new private `app-layer-INDEX-SUFFIX` folder
outside git. PNG is the baseline; absent optional formats stay unavailable.
Use only authenticated local browser controls, not an agent device route.
Stop the bounded recorder before disconnect/cleanup; its exports remain
renderer-only, not full-TV/video-plane recordings. See the guide's
[recording boundaries](../../docs/getting-started.md#debug-and-private-viewer).

For app-side optional keys, load the input-device lesson; browser injection
does not test native registration. Respect current app/product policy and the
canonical builder's canvas acceptance requirements when available. If a host
cannot open that canvas, say unavailable rather than replacing TV evidence.

Evidence: [capture correction and remote privileges](../../docs/lessons.md).
Windows and this toolkit's named-TV acceptance remain unverified.
