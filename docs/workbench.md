# TV Workbench — unreleased standalone tool

Run the remote, native paint preview and scoped inspector without Plex,
Copilot, npm or a web build. This is a separate entry point in tizen-tv-builder,
not a new repository or a replacement for the published 0.1.0 plugin.

## Start on macOS or Windows

Use an existing **Python 3.10+** installation. From this checkout:

```text
python3 tools/workbench.py
```

On Windows:

```text
py -3 tools/workbench.py
```

The command opens a private local browser URL. `--no-open` prints it instead;
the fragment contains a bearer capability, so do not share it or terminal logs.
The server binds only to IPv4 loopback on a random port. Ctrl-C stops it.
No SDK, tool, browser, certificates or packages are installed.

For an existing SDK outside the normal PATH, `TIZEN_SDK_ROOT` is supported.
Or pass `--sdb` with an absolute installed `sdb` / `sdb.exe` path. Standard
locations include the user's `tizen-studio` and `C:\tizen-studio` on Windows.
Paths with spaces are passed as process arguments, never through a shell.

The workbench's SDB path is cross-platform; the builder's existing
package/install wrappers and Copilot canvas remain POSIX-only. Windows host
execution passed on a hosted Windows Server image; desktop Windows 10/11 and
real Windows-to-TV behavior remain untested.

## First-run workflow

1. Check SDB and Web CLI readiness. Install Tizen Studio, Web CLI and Samsung TV
   Extension with the computer owner's permission if missing.
2. Enable TV Developer Mode using Samsung's documented Device Manager workflow.
   Resolve host Local Network/firewall permission normally; no relay, TLS or
   security bypass is created. Click **Find TV & apps** directly; no discovery
   permission checkbox is needed. One connected IPv4 TV is selected automatically; choose
   explicitly when several are connected. Discovery reads SDB devices and the
   selected TV's installed-app list under a short exclusive lease.
3. Use Samsung Certificate Manager to prepare author/distributor certificates
   and the TV DUID; permit installation and install your signed development
   app in Device Manager. The workbench does not read or manage certificates.
4. Choose the detected installed app and click **Open app**. Debug setup stops
   its package first, then debug-launches it. The legacy
   trailing `0` option is opt-in, based on the observed 2017 workaround.
5. A single discovered page is selected automatically. If several exist, choose
   one in **Advanced setup**. Only numbered page choices are shown, not titles,
   URLs, tokens or DOM. SDK pairing is not automated. Manual TV/app fields and
   an existing-inspector attach path remain available in Advanced.
6. Leave safe OK IDs empty unless you audited those DOM controls and handlers
   for non-write behavior. Authorize exclusive lane ownership, private pixels
   and input separately, then Connect preview.
7. Refresh manually or Start feed (discrete 10–60 second captures). Pause stops
   future captures. Runs end after at most 120 seconds and release the lane.
   Disconnect, then Release owned forward, or Ctrl-C to attempt owned cleanup.

Existing loopback forwards can be attached without SDB: authorize the port,
Discover pages, select a page and connect. Such forwards are never removed here.
This also lets an owner manage debug setup through Samsung's full Web Inspector.
The workbench provides discovery, capture provenance/latency and guarded CDP
input, **not** a full DevTools console, DOM editor or network recorder.

## Input and capture boundaries

D-pad, OK and Back have on-screen and keyboard routes. Focus the remote or
preview; arrow keys navigate, Enter/Space is OK and Backspace is Back. Editing,
IME, modifiers, held repeats, disconnected and busy sessions cannot dispatch.
There is no waiting input queue. OK is default-denied and checks the current
active element against audited IDs immediately before each input.

The check is not an app sandbox: IDs can be reused and handlers can change.
Audit even D-pad and Back before granting input to an unfamiliar app.
The full Samsung-style remote includes Power, Source, digits, rockers, Mute,
Home, Menu, Settings, Info, Guide, Exit, media and colour keys. A fixed read-only
`tvinputdevice.getSupportedKeys()` query supplies optional key codes; no guessed
codes or arbitrary evaluation are used. Each exposed app key sends matched
CDP keyDown/keyUp events. Extra keys require **Allow app actions, including
playback** in addition to the input grant. Unexposed keys are quietly dimmed
with tooltips. The workbench does not register keys or change the app's handlers.
This is app-key injection, not a Samsung system remote: Power/Source/Home may
remain reserved or unexposed, and successful injection does not prove system
behavior. UI tests never exercise real media-account writes.

The bootstrap capability moves from the URL fragment to tab-scoped
sessionStorage, allowing a local refresh without publishing a token in request
URLs or browser history. It is not persistent credential storage.

Paint preview uses native `LayerTree` replay through the independently written
MIT backend. Layers remain separate, sequential and potentially incomplete.
No AVPlay video plane, TV/system menus, panel effects, HDR/audio or atomic
composite is captured. Failed capture and connection loss
do not substitute synthetic images. No arbitrary evaluation route exists.

## Ownership, privacy and failures

The canonical macOS lane is `/tmp/plx-samsung-device.lock`. Windows uses the
system temporary directory plus the same directory basename. Directory/token/
identity checks never break another owner's lock. These are **host-local**
lanes, not a distributed lock; owners using different computers must coordinate
exclusive physical-TV use themselves. Native preview holds a lane for its
bounded run; setup commands take short leases and release them.

SDB output stays in private process memory, is capped at 256 KiB and has a
20-second process deadline. Only owned client processes are stopped. Shared
SDB servers, relays and others' forwards are never killed/removed. A timeout
may leave uncertain device state: reconcile in Device Manager before retrying.
A created forward remains explicitly owned after discovery fails, enabling
manual Release. Removal requires an exact current serial/local/remote mapping;
if mapping changed or cleanup is blocked, delete nothing and report failure.

Forward allocation checks current mappings but SDB has no atomic
compare-and-create primitive here. The exclusive device lane is required.
Never run competing SDK clients outside that lane.

Profile data and serial/app identity live only in memory. Pixels stay in memory
unless the owner enables recording into a private output folder. No credentials,
cookies, certificates or DUIDs are saved. API and pixel requests
require the random bearer token, exact Host, same-origin checks, bounded bodies
and no redirects. Responses use `no-store`, `no-referrer`, strict CSP and
`frame-ancestors 'none'`; the standalone shell is not embeddable. The existing
Copilot canvas's public embedding contract is unchanged.

## Inventory and repository decision

The PlxNative inspector contains a mature Node CDP transport, live viewer,
browse-safe input and lifecycle helpers with upstream host/device evidence.
That implementation is GPL: it was inspected read-only and **not copied**
into this MIT repository. Its Plex navigation allowlist/account assumptions
are not a general tool contract.

This repository already contains an independent stdlib CDP/LayerTree transport,
bounded directory lease, authenticated service, guarded input, host fixtures
and a POSIX Copilot adapter. Keeping TV Workbench here reuses these seams and
the packaging/signing guidance without creating a second protocol/security
implementation or remote repository. A later standalone repository can extract
the MIT entry point once its product and host/device acceptance are stable.

Feedback collected from capturer source `b24bf995`: Chrome 47 needs uncompressed
WebSocket; screenshot unanswered, screencast false; native paint replay worked;
competing sessions can hide discovery; some detail/list transitions produced
protocol errors with no proven root cause. Historical target UE55MU6172,
Tizen 3.0 / firmware T-KTMDEUC-1410.1 is upstream evidence, not this tool's pass.
The full-device thread explicitly retained ownership for its remaining phases,
and this branch waited for its explicit all-phases-complete release.

## Host checks

```text
python3 -m unittest discover -s tests -v
node --check tools/workbench/ui.js
node tests/live-controls.test.mjs
```

For an already-running isolated Chromium/Edge profile, the browser regression
gate intercepts every API call with explicit host-only fixtures:

```text
node tests/workbench-browser.test.mjs /ABSOLUTE/ISOLATED_BROWSER_PROFILE http://127.0.0.1:WORKBENCH_PORT/
```

It checks single key dispatch/focus retention, held-key suppression, media
button routing, unexposed-key disabling, connection loss and desktop/mobile
overflow. It neither connects a TV nor changes account/playback state.

**Record** writes a bounded sequence from one selected native app layer.
Enable private-save permission, choose an existing private output parent,
FPS/size/length/layer/format in **Recording & export**, then start/stop with the
same Record button. PNG works without an encoder; installed ffmpeg capabilities
enable GIF/MP4/WebM and optionally WebP. Paths/receipts remain authenticated and
private. The file/folder names carry `app-layer-N`; see
[recording research, formats and security](recording.md).

Host fixtures prove contracts, not hardware compatibility. The Mac browser
onboarding can be checked with no connected TV. Real debug-launch/forward
setup and successive native captures now have the bounded evidence below;
input effects and desktop Windows execution still need separate acceptance.
Do not publish a new plugin release or certify a
model from this host-only candidate.

## Bounded retail observation, 2026-10-11

After the device owner explicitly released the lane, TV Workbench connected to
the established UE55MU6172 target (historical Tizen 3.0, firmware
T-KTMDEUC-1410.1, Chrome 47/protocol 1.1). The existing installed development
app was source `ea0d7c9b`; no installation, account or playback actions were run.
The toolkit base was `ca4db2b`, with the subsequently committed WebM pixel-format
fix exercised in the confirmation run.

Observed: kill-before-debug, a fresh returned debug port, exactly one page,
owned forward and uncompressed CDP connection succeeded. Two native LayerTree
captures each returned one paint layer (492/476 ms in the confirmation run).
A 12-second recording admitted nine genuine frames; PNG, GIF, MP4 and WebM
exports completed. The first WebM attempt exposed incompatible native PNG
pixel-format negotiation; explicitly encoding `yuv420p` fixed it, also checked
offline with the saved native bytes. No video plane or full panel was captured.

Unaudited OK was correctly refused. The settled focused button did not match
the available read-only label allowlist, so arrow/Back/media injection effects
were **skipped**, not fabricated or bypassed. A later lane release (installed app
source `e8b3b4ab`, toolkit `f2ba970`) ran an arrows-only pass: after confirming
the focused element was not editable, injected Down moved focus and Up returned
it to the original geometry; OK was again refused (`guard`). Back, media and
other keys were not injected. App-key support and physical remote/system/IME
behavior remain different acceptance gates. TV network discovery on this LAN
was not exercised.

Both runs stopped their source, removed only their own forward, disconnected
their own loopback SDB connection and stopped their relay. The absent lane and
closed relay listener were independently checked. Private PNGs/recordings and
receipts remain outside git. This narrow observation is not all-model support,
signing/install acceptance or Store approval.

The private branch's `workbench-windows.yml` workflow runs the full suite and
`tests/windows_workbench_smoke.py` on `windows-latest`. The native gate starts
only a loopback server, checks authenticated routes, records original fixture
PNGs through the real Windows ACL backend, exports PNGs and cleans up. It
installs no SDK and performs no TV discovery or account calls. A green run is
hosted Windows image evidence, not desktop Windows 10/11 or TV acceptance.

On 2026-10-11, [run 38127747264](https://github.com/dragoshont/tizen-tv-builder/actions/runs/38127747264)
passed at source `48862db` on Windows Server 2025 (`windows-2025-vs2026`):
108 tests completed, with seven POSIX-only SDK/canvas contracts explicitly
skipped. Authenticated setup/state/record, native private ACL/fixture PNG/export,
and owned process/file cleanup all passed. No TV or SDK was contacted.

## Find another TV

**Find nearby TVs** sends bounded SSDP searches for DIAL, Samsung
RemoteControlReceiver and UPnP MediaRenderer. Each candidate is confirmed through
`http://TV_IPV4:8001/api/v2/` using its UDP sender address, not an untrusted
advertised LOCATION URL. Requests stay on private local IPv4 ranges, never
follow redirects, and cap peers, response sizes and deadlines. Unconfirmed
responders are reported rather than silently treated as TVs.

TV names/model/OS/firmware/developer mode/year are shown only when returned.
Missing fields remain **unknown**; the model number is not used to invent a
year and this endpoint does not reliably expose every field on every model.
DUIDs and other extra REST fields are discarded. Discovery reads metadata;
it does not enable Developer Mode, pair, install or change a certificate.

Select a discovered TV, follow **First time?**, and use Device Manager to enable
Developer Mode for the host PC's IPv4 address, obtain the new target's DUID,
create/update the Samsung distributor certificate with that DUID and permit
installation. Use [the new-app guide](getting-started.md) and
[prerequisites](prerequisites.md) for human/agent checks. A manually typed
private TV IP can be checked in Advanced. Router multicast filtering can block
SSDP even when manual REST/SDB connection works.
