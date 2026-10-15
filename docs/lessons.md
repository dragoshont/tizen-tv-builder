# Lessons from a real development week

These are independently expressed findings from the October 2026
[PlxNative Samsung investigation](https://github.com/dragoshont/plx-native/blob/84712aa54a687e323bc00de46713bd978c94732e/docs/samsung-tizen-poc.md).
They are not transplanted client code. Named hardware observations belong to that project,
not a completed device run of this new toolkit.

## Deployment and host

- Real SDK-signed `.wgt` signatures percent-encode Reference URIs (`css%2Fapp.css`) and
  wrap DigestValue base64 at 76 characters. A checker must decode both or it reports a
  valid package as tampered (`tizen.py check-package` does).
- The Samsung TV emulator image is x86_64 and needs HAXM, WHPX or KVM. On Apple Silicon
  `em-cli` exits with "cannot support HW virtualization"; use an Intel/AMD host or a TV.
- `typeof window.fetch` is feature detection, not network use; only real calls need the
  internet privilege.
- A public retail-TV Web app uses `.wgt` and Samsung signing; Linux underneath does not confer
  root, a general shell, native daemon deployment or LG ABI compatibility.
- The observed package identifier was ten alphanumeric characters. Keep app ID/package ID
  consistent in the manifest and run command; the display name determines package filenames.
- Missing install permission produced error 118; do not diagnose every failure as bad signing.
  Distributor certificates bind target DUIDs. Do not publish certificates or DUIDs.
- Certificate Manager and SDB server ownership can interfere. Never kill shared servers blindly.
- macOS Python/Node sockets returned `EHOSTUNREACH` while `nc`/`curl` reached the same
  target in a later run. The source/permission cause was **not established**. Check normal
  application permissions, routes and firewall observations without declaring a proven
  cause, changing a correct wired Host PC IP or creating a privacy-bypass relay.
- Installed x86 emulator images did not establish execution on Apple Silicon. GPU-off does not
  solve absent x86 CPU virtualization. Check shipped vendor architecture support, not announcements.
- One physical TV means one exclusive device worker. Build and inspect artifacts outside the
  device lease. Only remove forwards/processes you own.

### Vemm deployment observation (15 October 2026)

Parent-reported, not rerun by this toolkit readiness track: Vemm 0.0.1 from source
`2fc024c8` installed on QE65Q80CATXXH (2023), with an existing signing profile that
already covered the target. The observed OS was Tizen 9.0; the annual 2023 baseline
is not the set's updated runtime. `tv launch` reported Started and the TV's app list
confirmed installation. `tv open` failed because `LayerTree.makeSnapshot` was rejected
by that target. No device screenshot or runtime CSS receipt was obtained. Keep successful
install/launch and optional capture failure separate; `tv launch TV APP` remains the
explicit no-capture path. The older Plx app was retained, not uninstalled.

SDB can print a connection failure and exit zero. Inspect the output **and** require
the exact serial's `device` state; `offline`, a listed serial or scan reachability
does not establish a usable developer connection. A missing network response does not
prove power-off, Developer Mode off or a wrong Host PC IP.

Connection succeeded after a full TV reboot, a user-authorized SDB server restart,
opening Samsung Device Manager and waiting eight seconds. Several things changed;
which one caused recovery is unknown. This is not a restart recipe: never automate
`kill-server`, reboot or shared-server cleanup without exact authorization and no
conflicting owner. The original wired interface address was correct; advice to
replace it with another interface's address was wrong. Private addresses, certificate
values and device IDs are intentionally omitted here.

Household Guest profiles were real accounts and no account writes were authorized.
These operational observations do not establish playback, physical-remote fidelity,
Store approval or all-model support.

A subsequent parent-owned smoke used toolkit main `47b96b1` with that same Vemm
source on QE65Q80CATXXH/Tizen 9.0. It verified the exact app identity/version,
app-list presence, debug launch (1918 ms for that one run), a 1920x1080 body without
horizontal overflow and AVPlay state `NONE`. It sent no keys and read no content,
storage or screenshots; no backend operation was performed. The owned inspector
forward was verified removed, the pre-existing SDB connection preserved and the
canonical Samsung lane released. This is a narrow runtime/geometry receipt, not
capture, playback, physical-remote, firmware or benchmark acceptance.

The final parent-reported run supersedes that base-only smoke for current product
identity: source `2b50628cd9a346dbe1d374e103124665b749b260`, Vemm 0.0.1, the same
QE65Q80CATXXH/Tizen 9.0 and unchanged signing profile. The TV confirmed installation
and live appInfo version 0.0.1; the inspector reported the profile-picker/BlueCalm
state at 1920x1080 without errors or overflow, with AVPlay `NONE`. A final-source
rebuild matched all 50 installed payload-file hashes. The owned forward and canonical
lane were released. This still includes no remote, media/account action, physical-panel
or capture acceptance; no device work was performed by this toolkit readiness track.

## Inspector: important correction

Stop the authorized package before debug-launching the full app ID, and rediscover
after every launch. Inspector ports are ephemeral; successful discovery on an old
port does not establish the intended app identity. The standalone workbench owns
this stop/debug/forward sequence; `tizen.py debug` alone does not stop first.
Keep a lease across one coordinated run, not host-side edits. Short per-command
leases do not by themselves prevent another computer changing the physical TV.

The 2017 target initially answered `sdb shell 0 debug APP` with `closed`. This did **not** prove
that debugging was unavailable. Adding explicit trailing `0` launched a debug port despite
an unrecognized timeout-option warning. A similar older-device workaround was reported in
[Samsung/Wits#11](https://github.com/Samsung/Wits/issues/11).

Observed target: UE55MU6172, Tizen 3.0, Chrome 47.0.2526.69 protocol 1.1. HTTP target discovery
worked. An uncompressed WebSocket client worked where the newer built-in client's handshake
did not. Runtime evaluation and Page.enable answered; Schema.getDomains was absent.
Page.captureScreenshot was recognized but never returned an image within bounded attempts.
Page.canScreencast returned false, start accepted without frames and stop answered.

Therefore: inspect live CSS/geometry/errors, but do not advertise Page screenshots or
screencast recording on this target. Samsung documents debugging on TVs since 2015,
not every DevTools protocol method. Debugging can change application lifecycle;
no automatic reboot as cleanup.

### Capture update: keep the artifact types separate

**Upstream device observation, 2026-10-11; parent reviewed, not rerun by this toolkit.**
On the same Chrome 47 TV engine, the PlxNative investigation reports
`LayerTree.makeSnapshot` / `LayerTree.replaySnapshot` returning native renderer-paint
PNGs (1920x1080, 226x64 and 28x28). At the same time, Page screenshot requests timed out
even after 90 seconds while geometry RPCs remained responsive, and
`Page.canScreencast` returned false.

This corrects a blanket "native image capture is impossible" conclusion. Paint-layer
replay is real renderer output as reported by that study, **not** a panel/compositor
capture, a video-plane capture, a DOM reconstruction or a TV recording. Visual
completeness is not established. The parent inspected a privacy-masked 1920x1080
preview showing the actual sign-in UI, with separate button/spinner content missing.
Three native PNGs totaled 118,765 bytes; snapshot release and LayerTree disable
were confirmed. The published 0.1.0 bundles no replay implementation. The
unreleased [standalone canvas candidate](live-canvas.md) now implements the
protocol independently, with host mocks only; it claims no TV capture of its own.

The parent also reports a one-shot retail SDB `printf` attempt returned `closed` with
no marker. A framebuffer recipe requiring root and `enlightenment_info` was not run.
Do not promote it as a retail solution. Keep implementation and sanitized evidence in
the [current Samsung development process](https://github.com/dragoshont/plx-native/blob/84712aa54a687e323bc00de46713bd978c94732e/docs/samsung-tizen-development-process.md);
do not copy its GPL inspector into this MIT toolkit.

## Old engines, privileges and remote keys

- Tizen 3.0/M47 and Tizen 4.0/M56 are old Chromium generations. Prefer ES5 shipped
  output for these targets; separately probe the runtime APIs you actually use.
  The upstream 2017 probe found fetch/Promise but no WebAssembly or async-function
  syntax; `class`/`let` behavior also depended on strict mode. A WebKit-style
  user agent did not override the observed old V8/Chromium behavior.
- Samsung ProductInfo calls require the Samsung WebAPI script
  `$WEBAPIS/webapis/webapis.js` and
  `http://developer.samsung.com/privilege/productinfo`. Its API documents
  `SecurityError` when permission is absent. Diagnose loading/privilege/version
  before concluding that the TV lacks the feature. Never emit `getDuid()` into
  public logs. The hello template deliberately does not call ProductInfo.
- ArrowLeft/Up/Right/Down, Enter and Back are mandatory app keys; they need no
  registration and **cannot** be registered with `registerKey`. Optional keys
  come from `tizen.tvinputdevice.getSupportedKeys()` (which omits mandatory keys);
  register only supported names with `registerKey`, using
  `http://tizen.org/privilege/tv.inputdevice`. Missing privilege can raise
  `SecurityError`; unsupported names can raise `InvalidValuesError`.
- Preserve baseline D-pad/Enter/Back when discovering optional codes. A complete
  Samsung remote button inventory is not evidence that every key is exposed:
  leave unexposed actions dimmed. Home/system behavior is not equivalent to a
  browser key event; injected events do not prove native remote registration,
  app exit or system navigation. Extra app actions require the corresponding
  consent and current handler audit; a discovered key is not a permission grant.
- New-TV discovery metadata is device-dependent. Bounded SSDP DIAL,
  RemoteControlReceiver and MediaRenderer candidates need Samsung confirmation;
  the private `:8001/api/v2/` endpoint does not guarantee model/name/OS/firmware/
  Developer Mode/year. Leave missing fields unknown and record the provenance
  of owner/Settings observations. Never scan while a sibling device lane is live.

Sources: [upstream P0 probe](https://github.com/dragoshont/plx-native/blob/84712aa54a687e323bc00de46713bd978c94732e/docs/samsung-tizen-poc.md),
[Samsung ProductInfo](https://developer.samsung.com/smarttv/develop/api-references/samsung-product-api-references/productinfo-api.html),
[Samsung TVInputDevice](https://developer.samsung.com/smarttv/develop/api-references/tizen-web-device-api-references/tvinputdevice-api.html)
(API references read 2026-10-11). Discovery policy above describes the unreleased
toolkit contract, not an upstream all-model observation.

## Media and accounts

- MSE, HTML video and AVPlay are different paths. Native video planes can be absent from UI
  screenshots even when capture works. Panel, HDR and audio need real observation.
- 2018 documented DTS restrictions are not contradicted by narrow DTS-core playback observations
  on a 2017 set. Don't generalize either result across model groups.
- Reject token-bearing redirects. Unrestricted XHR is not a safe drop-in for unsupported fetch.
  Apply full-body deadlines, byte limits and cancellation, not only header timeouts.
- Private asset artwork belongs outside demo fixtures. Synthetic/original art makes public UI
  recordings safe; label desktop footage honestly.
- Plex Guest/managed profiles are real household users, not disposable tests. Use mocks for UI
  capture and explicit test records for protocol writes. Immich needs its own asset/auth contract.
- Treat credential persistence as an explicit product choice. The studied client documented
  unencrypted Web Storage for remember-me; leave it off on shared TVs, clear it on sign-out
  and surface deletion failure. That is a client-specific limitation, not a claim about every app.

## UI quality and evidence

Actual photos caught pseudo-icons placed over labels after a container padding override.
DOM contract tests alone missed it. Test measured text/icon separation and look at both
720p/1080p captures; decorative icons should not corrupt accessible button names.
Count decoded images as active resource owners, not only pending downloads; revoke Blob URLs
after decode and cancel jobs on view exit. Returning from detail must preserve pagination loaders,
cursors and focus. Large career/library searches need explicit paging and honest safety limits.

Three reviews are useful only if they test different failure modes: adversarial plan assumptions,
implementation identities/lifetimes, and frozen-tree evidence/documentation. Repeating a green mock
suite three times is not three independent proofs.
