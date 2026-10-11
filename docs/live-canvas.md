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

Opening starts an **unauthenticated**, loopback-only local service (user decision:
no token prompt). **No inspector connection or device lease is acquired until the
human clicks Connect.** Every request must name Host `127.0.0.1:<port>`; duplicate
headers are refused. API and frame requests are refused when the browser marks them
`same-site` or `cross-site` (this also blocks other localhost ports), and every POST
must carry this page's exact `Origin`. The four public GET routes (`/`, `/ui.js`,
`/ui.css`, `/controls.mjs`) contain no capability, profile or pixels.
**Residual risk:** any local process or other local user on this computer can call
the portal API while it runs (consent grants are JSON flags, not secrets). Run it
only on a single-user machine and stop it when done. The standalone Workbench
(`tools/workbench.py`) keeps its URL-fragment bearer token.
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

The primary button above the TV list runs a user-triggered, bounded scan. It
probes each local private /24 (at most 4 networks) concurrently for TCP 26101
(Developer Mode) and TCP 8001 (Samsung's read-only TV API), sends one SSDP
search, and reads the current `sdb devices` list. It writes nothing, never runs
`kill-server` and never reboots. A host answering only 8001 or SSDP is listed
only if its unauthenticated `http://<tv>:8001/api/v2/` reply says it is a Samsung
TV, so an unrelated open port is never shown as a TV. This is how TVs with
Developer Mode off appear. Several observations of one TV (for example 26101 and
8001 on the same address) merge into one card by the TV's own ID, which is used
only to derive the hashed alias and is never shown. The card is named from
`device.name` and `modelName` (e.g. "Samsung 7 Series (55) · UE55NU7…"),
otherwise "Samsung TV (2018) #1". Rename (✎) stores your name in the 0600 profile
keyed by the hashed alias, never by address.

Discovery limits: TVs that are off or in deep standby, on another subnet/VLAN or
guest network, or that block both ports and SSDP do not appear. At most 16 TVs
are listed. This discovery path is host-tested with mocks only and is
**unverified** on real TVs.

### Developer Mode labels

Each card and `tv scan`/`tv check` show one developer-readiness label with a next
action. It is separate from Connected / Not connected / In use, and comes from
observed evidence only:

| Evidence | Label |
|---|---|
| Developer connection accepted (this session or sdb lists it) | Dev mode enabled |
| Port 26101 open, not tried yet | Dev mode reachable · not checked (Connect to check) |
| Port 26101 open but the TV refused this computer | Needs to be configured (Host PC IP, restart TV) |
| The TV API reports Developer Mode off and port 26101 is closed | Dev mode not enabled |
| The TV API answers, does not report the mode, and port 26101 is closed | Dev mode port closed (off or blocked; not proof) |
| Nothing answered | Not reachable (unknown, never "not enabled") |

An open port alone is never "Dev mode enabled". Certificate/install readiness is a
separate line that appears only after Identify.

### TV details (shared by portal and CLI)

Scan builds one metadata record per TV, used by both the portal and the CLI:
manufacturer, friendly name, full model, model family, **model year** with its
provenance (reported by the TV, or inferred from the model code), OS name and
version, a platform baseline, and resolution, The Frame and firmware only when the
TV reports them. Model year is not a manufacture date; the manufacture date stays
unknown. The OS version is shown only when it was observed over the developer
connection (Identify); otherwise it reads "Tizen · version unavailable". The
baseline ("2019 models shipped with Tizen 5.0") comes from Samsung's
[TV Model Groups](https://developer.samsung.com/smarttv/develop/specifications/tv-model-groups.html)
table and is never shown as the TV's version, because regional exceptions and
upgrades exist (for example 2017 ENTRY_CN1 ran Tizen 2.4). Model codes are parsed
with anchored family patterns (LED `UE43RU7092` → 2019, QLED, Neo QLED, OLED, The
Frame/Serif/Sero); unknown or ambiguous codes give "Model year unknown".

Each TV card shows one of four states. **Connected** means both a developer
link and a current app picture that the page has decoded; a non-empty list of
returned layers is not enough. Nothing else earns that label, and the page fails
closed (keys stay off) if the helper reports Connected without a decodable,
current picture. **Developer Mode ready** means the developer link works
but no preview is attached ("Preview not connected"). **Not connected** and
**Busy** (another session holds its lock; read-only) cover the rest.

Connect has no confirmation dialog: it launches no app and sends no keys. It runs
`sdb connect` and Identify under that TV's own lease
`/tmp/tizen-tv-<alias>.lock`, so it never preempts the canonical lock. If the
canonical lock is present the portal shows a caution and you must type the alias. When Connect fails the
card explains why and offers Retry.

From Developer Mode ready, **Open app for preview** takes an app ID and always
asks first, because it restarts that app on the TV in debug mode; the portal
never does this silently. The portal forwards the app's inspector to loopback, attaches,
captures, and shows Connected only after the page decodes that picture. The picture is the app's own drawing
(renderer paint layers), not the whole TV screen, other apps, AVPlay video, HDR
or audio. Arrows and Back reach the app; OK is off because scanned apps have no
audited safe buttons. Disconnect ends the preview and removes only the forward
this portal created. Certificate, package and install controls live under
Advanced; install needs the alias typed and a matching DUID. Addresses, DUIDs and
lock paths appear only under Details, redacted.

The left list shows each TV compactly (name, model · year, status). Selecting a
row shows that TV on the right without contacting it; the TV has one Connect
button. Its card shows model · model year · OS, the Dev mode label with the next
action, and Details with provenance.

### Same features from the terminal: `tizen.py tv`

No setup is needed. On first use the kit creates an empty private profile at
`~/.tizen-tv-builder/profile.json` (folder 0700, file 0600, `{"sources": []}`, no
credentials); set `TIZEN_TV_PROFILE` or pass `--profile` to use another one.

```sh
python3 tools/tizen.py tv scan                # find Samsung TVs; shows model, model year, OS, Dev mode label
python3 tools/tizen.py tv list                # last scan again, without contacting any TV
python3 tools/tizen.py tv check tv-abc123     # is Developer Mode really working?
python3 tools/tizen.py tv connect tv-abc123   # connect, identify, then disconnect
python3 tools/tizen.py tv rename tv-abc123 "Den TV"
python3 tools/tizen.py tv portal              # prints http://127.0.0.1:47811/ (or a free port)
```

First run, from a terminal (unreleased; host-tested against a fake SDK):

```sh
python3 tools/tizen.py tv setup                     # sdb, tizen CLI, signing profiles; installs nothing
python3 tools/tizen.py tv cert tv-abc123            # which certificate; saves the DUID privately (0600)
python3 tools/tizen.py tv duid tv-abc123 --out ~/duid.txt   # hidden unless --show or --out
python3 tools/tizen.py tv install tv-abc123 app.wgt --sign MyTV --yes
python3 tools/tizen.py tv launch tv-abc123 AbCdEf1234.App --yes
python3 tools/tizen.py tv remove tv-abc123 AbCdEf1234.App --yes
```

Each command connects, acts, and disconnects what it connected. `install` re-signs a
private copy named `app.wgt` (the SDK names its output after `<name>`, which may contain
spaces) and passes the TV's device name with `-t`; without `-t` the Tizen CLI silently
does nothing. In Developer Mode the default Tizen distributor certificate installed on
the 2017 and 2019 TVs tested; other years are reported as unverified. The DUID comes
from the developer link (`getduid`), not the network API's `duid`. `remove` re-lists
the TV's apps (`0 vd_applist`) and only reports success when the app is gone: on
Tizen 5.0 `tizen uninstall` exits 0 and changes nothing, so the command then says to
delete the app on the TV (Apps, gear, the app, Delete). If the list cannot be read the
result is "unconfirmed", never success, and so is a list shorter than its own
"apps count". The listing itself was checked read-only on the 2017 (Tizen 3.0) and 2019
(Tizen 5.0) TVs; the other first-run commands are not yet verified on a TV. Python 3.10 or newer is required; older versions stop with that message.

`check` reports each stage separately and only says "Developer Mode ready" when
all of them pass: Developer Mode port open (a TCP answer only), developer
connection accepted by the TV (`sdb connect` listed as a device), and identity
read (Tizen version; the DUID is only reported as present, never shown). An open
port with a refused connection is "Not ready": Developer Mode is on, but the TV's Host PC IP most
likely points to another computer. A TV that reports Developer Mode off is told apart and
not connected to. Busy TVs
are not touched. `tv-abc123` is the alias printed by `scan`; aliases stay stable
between runs, and `list` keeps only names and status from the last scan
(`last-scan.json`, 0600), never addresses or identities.

`portal` starts the same page the canvas shows, on loopback only, and does not
scan or contact a TV until you choose Scan. Further commands for configured
inspector targets: `key SOURCE down back --yes` and
`screenshot SOURCE --out ~/private-shots --yes`. `python3 tools/tv.py ...` is the
same CLI.

The CLI calls the same Fleet/Source code as the portal, accepts aliases only (never
raw addresses), keeps the key allowlist and the OK safe-target gate, writes
screenshots 0600 outside this repository only, and releases every connection,
lease and forward before it exits. Use `py tools\tizen.py tv ...` on Windows; the
private profile (rename, key, screenshot, portal) requires POSIX permissions.
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
node tests/portal-browser.test.mjs http://127.0.0.1:BROWSER_CDP_PORT http://127.0.0.1:47811/
```

The last gate uses an already-running isolated browser with DevToolsActivePort.
It checks the real offline renderer and injected, explicitly labelled **UI-only**
fixtures for shortcuts/backpressure. Fixtures never connect a TV or synthesize
runtime fallback captures. Three timed host feed cycles use a mock CDP provider.
The portal gate answers every `/api/` request with in-page synthetic TVs and
checks that Connected and the keys stay off for empty frames, undecodable images
and a frame that arrives after the connection is lost. Set
`PORTAL_BASELINE=<revision>` to replay an older `ui.js` and show the checks fail
there.

Host pass is not device acceptance. Parent/capturer must release the existing
viewer, select the authorized page, then observe at least three genuine 10-second
captures, Start/Pause/manual Refresh, guarded navigation and cleanup on the named
model/firmware. Input effect, physical fidelity, capture completeness and actual
latency remain pending. Do not publish before that acceptance.
