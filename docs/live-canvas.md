# Live TV canvas — unreleased candidate

This is a standalone Copilot extension, not a PlxNative feature. Its native
provider is independently written Python 3.10+ standard-library code. The thin
Node adapter uses the host's bundled SDK; do not install SDK npm packages.
The published 0.1.0 plugin is unchanged. **No new release or installed-plugin
replacement until parent acceptance.** Identify this candidate by its commit.

## Install and open

In this toolkit checkout, `.github/extensions/tizen-live/` is discovered as a
project extension. Use the host's `extensions_reload`, then
`extensions_manage(operation="inspect", name="tizen-live")`.

For another project or an explicit user installation:

```sh
# The destination parent must exist. Use a NEW directory; no overwrite/update.
python3 /absolute/toolkit/tools/install_canvas.py \
  --destination /absolute/app/.github/extensions/tizen-live
# Or choose an existing $COPILOT_HOME/extensions parent for a user-scoped install.
```

This copies renderer assets, the SDK entry point and its Python backend. It does
not start a service, connect a TV, install tools or reload the target session.
Reload extensions there, or start a fresh supported session. Plugin packages
include the adapter source; **plugin installation alone is not extension
registration**. Project adapters shadow user adapters of the same name.
Windows live-provider installation is not supported by this POSIX candidate.

Runtime contract:

```text
canvasId: tizen-live
open input: {"profilePath": "/absolute/private/tv-profile.json"}
instanceId: your-own-stable-panel-handle
action: status
action input: {}
```

Call `list_canvas_capabilities`, `open_canvas`, then
`invoke_canvas_action(instanceId=..., actionName="status", input={})`.
`status` reads connection, capture age/provenance, metadata and capabilities only.
It performs no device RPC and returns no endpoint, page identity, DOM, pixels or
authentication token. There is no agent-callable mutation action.

Opening starts only an authenticated, loopback-only local service. **No inspector
connection or device lease is acquired until the human clicks Connect.**
The four exact public GET routes (`/`, `/ui.js`, `/ui.css`, `/controls.mjs`)
allow cross-site embedding by the host. They contain no capability, profile or
pixels. Host/duplicate-header checks still apply; API/frame requests and every
POST retain bearer authentication and strict origin/cross-site rejection.
SDK open/status success alone does not prove the embedded page rendered.

## Private profile

Keep this file outside the toolkit/repository, owned by your user, mode 0600.
Configured `sources` use an existing authorized loopback inspector forward/relay;
that provider does not launch SDB or create relays/forwards. TVs found by
**Scan network for TVs** need no profile entry (see below), so `"sources": []`
is a valid profile. Replace these placeholders privately; never commit actual
addresses or identities:

```json
{
  "sources": [{
    "id": "living-room",
    "kind": "tv",
    "endpoint": "http://127.0.0.1:12345",
    "target_id": "EXPLICIT_PAGE_ID",
    "lease_path": "/tmp/plx-samsung-device.lock",
    "lease_seconds": 120,
    "metadata": {
      "model": {"value": "", "provenance": "unknown"},
      "year": {"value": "", "provenance": "unknown"},
      "os_version": {"value": "", "provenance": "unknown"},
      "firmware": {"value": "", "provenance": "unknown"}
    },
    "safe_activation_ids": []
  }]
}
```

`lease_seconds` is the **maximum connected-run duration**, 10–120 seconds,
not lock TTL. The UI explicitly displays this cap and lets the user choose a
shorter run. At the deadline the client disconnects and ownership-checked cleanup
releases its lane. It never renews or automatically breaks a stale lease.
Choose at least 40 seconds to observe three default 10-second feed cycles.

Native sources must use the canonical `/tmp/plx-samsung-device.lock` atomic
directory. Acquisition is mkdir 0700, lstat dev/inode and exclusive owner.json
0600 with pid/UUID token. Release checks directory identity and token before
unlink/rmdir. Existing ownership fails immediately as `device_lock_held`; a
cleanup mismatch deletes nothing and reports `device_lock_cleanup_failed`.
**Do not run beneath an outer wrapper already owning this lane:** stop/release
that worker first. This candidate deliberately has no unchecked nested/no-op
ownership adapter. It never calls flock, kill-server or remove-all-forwards.
The external relay owner remains responsible for relay/forward/FIFO cleanup.

Metadata is optional and stored per profile/source, not panel ID. Editing in the
canvas is user-provided (or unknown); it does not detect model/year/OS/firmware.
`observed` is accepted only for independently verified primary-system metadata
prepared by the operator. The UI does not upgrade an entered value to observed.
The study's UE55MU6172/2017/Tizen 3.0/firmware report is not freshly detected here.

An explicit second source may use `kind: "emulator"` with its own distinct lane,
endpoint and target. TV remains the default when configured. Switching and
side-by-side comparison preserve separate labels and capture timestamps.
An unavailable source never substitutes another source's image.

Optional private keys: `tv_names` (alias → your name, written by Rename) and
`certificate_duids` (absolute path, outside the repository, to the distributor
device-profile file used only for the install DUID gate). Up to 8 sources, at
most one emulator; a TV lease is the canonical lock or `/tmp/tizen-tv-<id>.lock`.

## Scan network for TVs

The primary button at the top runs a user-triggered, bounded scan: each local
private /24 for TCP 26101 (Developer Mode) plus the current `sdb devices` list.
It writes nothing, never runs `kill-server` and never reboots. During Scan only,
it reads the TV's unauthenticated `http://<tv>:8001/api/v2/` `device.name` and
`modelName` with a short timeout to name the card (e.g. "Samsung 7 Series (55) ·
UE55NU7…"); otherwise "Samsung TV (2018) #1". Rename (✎) stores your name in
the 0600 profile keyed by the hashed alias, never by address.

Each TV card shows Connected, Not connected or Busy (another session holds its
lock; shown read-only). Connect asks once, then runs `sdb connect` and Identify
under that TV's own lease `/tmp/tizen-tv-<alias>.lock`, so it never preempts the
canonical lock. If the canonical lock is present you must type the alias. When
Connect fails the card explains why and offers Retry: Developer Mode steps (Apps
→ 1 2 3 4 5 → Developer mode On, Host PC IP = this computer's address shown on
the card, restart TV), or "Connected, install blocked: needs certificate update"
when the TV's DUID is not in `certificate_duids`. Install and run require typing
the alias and a matching DUID. Addresses, DUIDs and lock paths appear only under
Details, redacted; they are never shown on the card or written to receipts.
The remote and screen view work for configured sources; for scanned TVs the
remote stays off with an inline reason until an app is opened for inspection.
Scan, naming and guidance are host-tested with a mocked SDB only; real 2018 TV
wording, identify output and the 8001 fields are **unverified**.

## Human-operated capture and input

Connect shows one confirmation ("Screen images may show account info; remote
keys will be sent.") which grants the session, private pixels and input for that
run; the backend still checks each grant separately. Start feed requests a discrete genuine native LayerTree paint capture
every 10 seconds (adjustable 10–60); Pause stops future requests, and Refresh
captures manually. Pause does not cancel an already in-flight capture.
Frames remain private in memory. A failed/disconnected capture retains the last
real frame marked stale, with timestamp/age; no generated fallback exists.
Unchanged pixel hashes reuse browser Blob images instead of retransferring PNGs.

Each source admits one bounded operation, no waiting command queue. Input is
acknowledged before a subsequent pixel request; command RTT and capture
latency/age are separate. If a capture is already running, input is refused
immediately rather than queued. Capture budgets are bounded, with separate
bounded snapshot-release/disable cleanup. Closing the last panel, provider pipe
EOF, process cancellation or the run deadline disconnects owned clients.

Each card has its own on-screen Remote panel. Focus it or the screen view:

| Shortcut | Command |
|---|---|
| Arrow keys | D-pad direction |
| Space / Enter | OK |
| Backspace | TV Back |

Editing fields, IME/modifiers, held repeats, disconnected or busy input cannot
dispatch. Handled focused keys suppress browser defaults/native button clicks,
so Enter/Space does not double-send. Buttons use the same guarded command route.
There is no Home, Exit, volume, arbitrary evaluation or text-entry route.

OK/click are **default-denied**. `safe_activation_ids` must be an operator-audited
list of current app DOM controls whose activation cannot play media, scrobble,
change household state or perform other unapproved writes. Before each OK/click,
the provider checks the actual current activeElement/elementFromPoint ID through
a fixed read-only expression. An arbitrary string does not match an element.
This check is not a complete app behavior sandbox: IDs can be reused and DOM can
change between RPCs. Audit the target app's event handlers and navigation contract,
including D-pad/Back, before granting input. Do not add Play merely to enable OK.

Coordinate clicks additionally require a fresh matching-revision, viewport-sized,
zero-offset, untransformed layer. Partial/transformed/stale geometry is refused.
CDP input is synthesized; physical-remote parity is **unverified**. Back 10009
was observed in the separate study, including an app-specific no-op, not proved
for this provider or every screen. A successful RPC does not prove visible effect.

## Evidence and acceptance

The feed is native **renderer paint-layer replay**, not full panel/compositor,
video-plane, HDR/audio, DOM reconstruction or a recording. Separate layers may
omit controls and overlays. No Page screenshot/screencast fallback is assumed.
Discovery-advertised device hosts are never contacted: the validated page path
is connected only through the explicitly configured IPv4 loopback port.
Unsupported/malformed methods, disconnects and cleanup failures remain visible.

Host gates:

```sh
python3 -m unittest discover -s tests -v
node tests/live-controls.test.mjs
node --check .github/extensions/tizen-live/extension.mjs
# Optional: existing isolated Chromium browser, no installation or TV needed:
node tests/live-browser.test.mjs /absolute/private/browser-profile OFFLINE_CANVAS_URL
```

The last gate uses an already-running isolated browser with DevToolsActivePort.
It checks the real offline renderer and injected, explicitly labelled **UI-only**
fixtures for shortcuts/backpressure. Fixtures never connect a TV or synthesize
runtime fallback captures. Three timed host feed cycles use a mock CDP provider.

Host pass is not device acceptance. Parent/capturer must release the existing
viewer, select the authorized page, then observe at least three genuine 10-second
captures, Start/Pause/manual Refresh, guarded navigation and cleanup on the named
model/firmware. Input effect, physical fidelity, capture completeness and actual
latency remain pending. Do not publish before that acceptance.
