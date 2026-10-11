---
name: samsung-tv-lease
description: Coordinate exclusive Samsung TV ownership before connection, install, debug, input or capture; resolve a busy lane safely. Not for host-only builds or borrowing an LG lock.
---

# One TV, one bounded run

Resolve the toolkit at `../..` from this skill's reported base directory.
Read [Common contract](../common.md); it supplies permission and privacy holds.

1. Name the exact target, owner, operations, duration and cleanup. Check the
   existing Samsung lane and any owner on another computer before device work.
2. Build/test first. Book only deploy → launch → one case/capture → teardown,
   not edits, reading evidence or the entire working session.
3. Reuse the project's canonical ownership mechanism. Never assume `--authorized`
   in `tizen.py` acquires a lease; it is only an acknowledgment. If no compatible
   lane is available, stop device work and continue host-only.
4. In [TV Workbench](../../docs/workbench.md), setup actions acquire their own
   short leases; preview runs are capped at 120 seconds. Do not pre-acquire
   the same lane and cause self-contention. Coordinate the whole physical run
   so another client cannot change the app between short setup operations.
5. On busy, report the holder privately and wait/requeue; do not silently attach
   to their inspector, restart their app or steal their forward.
6. In finally/teardown, stop your input/feed, disconnect, release your identified
   forward, then release your lease. Verify cleanup; report uncertainty.

The toolkit lease (`tools/live_tv.py`) has no expiry: it is a directory holding
`owner.json` (pid, token), and release removes it only after checking that same
token and directory identity. A crash can leave it behind. A stale-looking lease
is not proof its owner is gone, so recovery is manual and owner-approved:
confirm the recorded pid is not running on this host, that no worker, inspector
forward or feed is still active, and ask the owner. Only then remove `owner.json`
and the directory. Never delete it automatically. Reacquire for the next run.
Host-local lease paths do not coordinate other computers.

Evidence: [upstream Samsung inspector ownership](https://github.com/dragoshont/plx-native/blob/dragoshont-samsung-tizen-feasibility/docs/samsung-web-inspector.md).
Upstream LG lease scripts are conceptual evidence only, not Samsung commands.
