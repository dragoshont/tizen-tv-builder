# Zero to a signed Samsung TV app

**What you can do:** copy an account-free Web app, validate it, build/sign with
an installed Samsung SDK, install it to one authorized TV and inspect it privately.
No npm or media account is required. See [prerequisites](prerequisites.md) first.
Host checks do not certify hardware support; this toolkit has not completed its
own named-TV acceptance. Windows SDK examples are instructions, not tested Windows
execution. No install, network scan or device operation was performed to write this guide.

## 1. Copy the minimal app

From this toolkit checkout, copy to a **new** owner-selected directory outside
the toolkit. Replace every capitalized placeholder before running commands.
Do not use temporary directories or put credentials in source.

macOS:
```sh
cp -R examples/hello-tv "/ABSOLUTE/NEW/HelloTv"
python3 tools/tizen.py validate "/ABSOLUTE/NEW/HelloTv"
```

Windows PowerShell:
```powershell
Copy-Item -Recurse examples/hello-tv 'C:\YOUR\NEW\HelloTv'
py -3 tools/tizen.py validate 'C:\YOUR\NEW\HelloTv'
if ($LASTEXITCODE -ne 0) { throw 'Manifest validation failed' }
```

Expected validation:
```json
{"app": "PlxHelloTv.Hello", "required_version": "2.3"}
```

Before making a new product, edit config.xml: a unique **ten-alphanumeric**
package, matching `PACKAGE.Application` ID, display name, content entry and a
deliberate minimum `required_version`. Keep IDs/author for subsequent updates.
The example's `2.3` floor is not a support claim. It displays runtime facts and
one focused Refresh button with no network calls or account state. Its ES5
JavaScript needs no API privileges; do not add ProductInfo/input privileges
until implementing those features. [Old-engine lessons](lessons.md) explain why.

## 2. Build the source without a TV

Use Samsung CLI directly for a persistent, inspectable build in your app
directory. Set paths to the **installed** SDK. No SDK install is implied.

macOS:
```sh
set -e
TIZEN="$HOME/tizen-studio/tools/ide/bin/tizen"
SDB="$HOME/tizen-studio/tools/sdb"
"$TIZEN" build-web -- "/ABSOLUTE/NEW/HelloTv"
```

Windows:
```powershell
$Tizen = 'C:\tizen-studio\tools\ide\bin\tizen.bat'
$Sdb = 'C:\tizen-studio\tools\sdb.exe'
& $Tizen build-web -- 'C:\YOUR\NEW\HelloTv'
if ($LASTEXITCODE -ne 0) { throw 'Unsigned build failed' }
```

Expect exit 0, a successful SDK build diagnostic and `.buildResult/config.xml`
plus `.buildResult/index.html` in that copy. Stop if absent; SDK text varies
by release. Do not treat an unsigned build as a widget/install/TV pass.
The POSIX `tizen.py build` alternative stages an ephemeral unsigned build;
Windows must not use that SDK wrapper.

## Samsung TV certificates

With the owner's existing scoped permission, use installed Certificate Manager:

1. Add a profile of type **Samsung → TV**, not generic Tizen.
2. Create/reuse the author certificate; preserve its identity for updates.
3. Create a distributor certificate containing the **target TV DUID**. Obtain
   the DUID through Samsung's UI/Device Manager, not a public diagnostic log.
4. Select your profile (`YOUR_PROFILE` below), keep `.p12`/passwords/profile
   files outside source, and close Certificate Manager before SDB work.

Follow [Samsung's certificate instructions](https://developer.samsung.com/smarttv/develop/getting-started/setting-up-sdk/creating-certificates.html).
Signing preparation does not authorize a TV connection or installation.

## 3. Sign and check the new artifact

macOS:
```sh
"$TIZEN" package -t wgt -s YOUR_PROFILE -- "/ABSOLUTE/NEW/HelloTv/.buildResult"
```

Windows:
```powershell
& $Tizen package -t wgt -s YOUR_PROFILE -- 'C:\YOUR\NEW\HelloTv\.buildResult'
if ($LASTEXITCODE -ne 0) { throw 'Signing failed' }
```

Expect exit 0 and an SDK-emitted `.wgt` path. Use **that new file**, not a
previous artifact. The `<name>` may determine a filename with spaces: quote
it. Package `.buildResult`, not a directory containing both source/build output.

Structural check with Python (replace the widget path):
```sh
python3 -c "import zipfile; z=zipfile.ZipFile('/ABSOLUTE/NEW/HelloTv/OUTPUT.wgt'); n=set(z.namelist()); assert {'config.xml','index.html','author-signature.xml','signature1.xml'} <= n; assert z.testzip() is None; print('Widget entries and ZIP integrity OK; signatures not cryptographically verified')"
shasum -a 256 "/ABSOLUTE/NEW/HelloTv/OUTPUT.wgt"
```

Windows:
```powershell
py -3 -c "import zipfile; z=zipfile.ZipFile(r'C:\YOUR\NEW\HelloTv\OUTPUT.wgt'); n=set(z.namelist()); assert {'config.xml','index.html','author-signature.xml','signature1.xml'} <= n; assert z.testzip() is None; print('Widget entries and ZIP integrity OK; signatures not cryptographically verified')"
if ($LASTEXITCODE -ne 0) { throw 'Widget structure failed' }
Get-FileHash -Algorithm SHA256 'C:\YOUR\NEW\HelloTv\OUTPUT.wgt'
```

Expect the exact structural message and a SHA256 digest. Signature XML presence
and checksum do **not** validate trust, expiry, DUID coverage or cryptographic
signatures. Inspect the archive for accidental private files before installation.
Successful SDK signing is a requested-profile result, not Store approval.

## New-TV onboarding

Do this for each new physical TV, not only the first:

1. In the current workbench, use new-TV discovery **only** within your granted
   LAN scope while no other device lane is active. Bounded SSDP candidates
   (DIAL, RemoteControlReceiver, MediaRenderer) need Samsung confirmation;
   a discovery advertisement alone does not establish the intended Samsung TV.
2. Where supported, the private Samsung `http://TV_IPV4:8001/api/v2/` response
   can confirm a candidate. Treat model, name, OS, firmware, Developer Mode and
   year as known **only if actually returned**; otherwise leave unknown and
   verify through TV Settings/owner observation with provenance. It does not
   always return every field. Do not publish raw responses or identifiers.
3. Select/confirm the exact physical target. In the TV's Apps Developer Mode
   screen enable Developer Mode and enter the **actual Host PC IP** of the
   permitted computer/interface. Restart only as the vendor setup requires
   and within the granted scope. Steps/keys vary by model.
4. Connect/pair through Samsung Device Manager or SDB below. SDB readiness is
   not a consumer remote-control pairing token; do not invent a pairing PIN
   command. Follow a target's actual prompts.
5. Obtain this target's DUID privately. Issue/update a Samsung TV distributor
   certificate covering it, retaining the app's author identity. Re-sign for
   the new distribution profile; never assume the old TV's certificate covers it.
6. Permit installation on **this** target and verify install/launch on it.
   Do not reuse the older target's debug port or acceptance results.

For a manually known target, discovery is optional. Follow
[Samsung device setup](https://developer.samsung.com/smarttv/develop/getting-started/using-sdk/tv-device.html).
The [workbench contract](workbench.md) owns its actual UI/control availability.
Already-granted scoped operations need not be re-confirmed per step; never break
a live lock. Coordinate physical ownership across computers, since locks are local.

## 4. Connect, permit, install and observe

Build/sign first; then use the canonical Samsung lane for one bounded run.
The following raw SDK commands do **not** acquire a lease themselves. Do not
run them concurrently with workbench setup/preview or somebody else's device run.
Replace `TV_IPV4:26101` and `TARGET_SERIAL` with the privately confirmed target.

macOS:
```sh
set -e
"$SDB" connect TV_IPV4:26101
"$SDB" devices
"$TIZEN" install-permit -s TARGET_SERIAL
"$TIZEN" install -n "/ABSOLUTE/NEW/HelloTv/OUTPUT.wgt" -s TARGET_SERIAL
"$TIZEN" run -p PlxHelloTv.Hello -s TARGET_SERIAL
```

Windows:
```powershell
& $Sdb connect TV_IPV4:26101
if ($LASTEXITCODE -ne 0) { throw 'Connection failed' }
& $Sdb devices
if ($LASTEXITCODE -ne 0) { throw 'Device enumeration failed' }
& $Tizen install-permit -s TARGET_SERIAL
if ($LASTEXITCODE -ne 0) { throw 'Install permission failed' }
& $Tizen install -n 'C:\YOUR\NEW\HelloTv\OUTPUT.wgt' -s TARGET_SERIAL
if ($LASTEXITCODE -ne 0) { throw 'Installation failed' }
& $Tizen run -p PlxHelloTv.Hello -s TARGET_SERIAL
if ($LASTEXITCODE -ne 0) { throw 'Launch failed' }
```

Use your edited full app ID if changed. Expected connection state: the exact
serial is listed as `device`. Device Manager's **Permit to install applications**
is the GUI equivalent of `install-permit`. Expect successful SDK install/run
diagnostics; output spelling varies. An exit code alone is not app acceptance:
observe **Hello Samsung TV**, a focused Refresh button and actual runtime facts
on the selected TV. Test physical Enter/Back and restart separately, keeping
back/exit behavior app-specific. This hello template is not a full navigation app.

Keep a private receipt: source SHA, widget checksum, SDK/host/model/firmware,
IDs, date, operation grants, actual observations and pass/fail/skip. Verify
owned teardown and release the lane immediately, before writing the report.
Update/uninstall/account testing require their respective scope; never delete
another installation or use a household media account to verify this template.

## Debug and private viewer

Prefer the [standalone workbench](workbench.md): start it with your installed SDB,
choose the exact installed app, then Launch in debug. It stops the package first,
launches debug, creates an owned forward and discovers current pages. Its private
loopback URL is a bearer capability; do not share it.

Manual SDK sequence for an already-authorized target under its lane:

macOS:
```sh
set -e
"$SDB" -s TARGET_SERIAL shell 0 kill PlxHelloTv
"$SDB" -s TARGET_SERIAL shell 0 debug PlxHelloTv.Hello
# Only for the relevant legacy workaround, replace the previous debug command:
# "$SDB" -s TARGET_SERIAL shell 0 debug PlxHelloTv.Hello 0
"$SDB" forward --list
"$SDB" -s TARGET_SERIAL forward tcp:LOCAL_PORT tcp:RETURNED_DEBUG_PORT
python3 tools/tizen.py inspect --port LOCAL_PORT
```

Windows:
```powershell
& $Sdb -s TARGET_SERIAL shell 0 kill PlxHelloTv
if ($LASTEXITCODE -ne 0) { throw 'Stop failed; reconcile before debug' }
& $Sdb -s TARGET_SERIAL shell 0 debug PlxHelloTv.Hello
if ($LASTEXITCODE -ne 0) { throw 'Debug failed; reconcile device state' }
# Relevant legacy alternative only: & $Sdb -s TARGET_SERIAL shell 0 debug PlxHelloTv.Hello 0
& $Sdb forward --list
& $Sdb -s TARGET_SERIAL forward tcp:LOCAL_PORT tcp:RETURNED_DEBUG_PORT
if ($LASTEXITCODE -ne 0) { throw 'Forward failed' }
py -3 tools/tizen.py inspect --port LOCAL_PORT
if ($LASTEXITCODE -ne 0) { throw 'HTTP discovery failed' }
```

Expected: this debug launch returns a port; verify the exact forward mapping
with `forward --list` and loopback `/json/version` + `/json/list` metadata.
Ports are ephemeral. Choose an unused local port, never overwrite an existing
mapping, and remove only your still-matching mapping in finally:
`"$SDB" -s TARGET_SERIAL forward --remove tcp:LOCAL_PORT` (macOS), or
`& $Sdb -s TARGET_SERIAL forward --remove tcp:LOCAL_PORT` (PowerShell).
Stop on timeout/uncertain state and reconcile in Device Manager before retrying.
Never kill shared SDB servers or use remove-all as cleanup.

HTTP discovery is not WebSocket/capture success. Paint preview is native
renderer-layer replay, potentially incomplete—not panel/AVPlay capture,
HDR/audio evidence or a full-TV recording. A recorded app-layer feed retains the
same renderer-only boundary. Remote button inventory is not proof every
key is injectable: only exposed/audited actions are enabled; extra app actions
need consent. See [runtime/input/capture lessons](lessons.md).

When the authenticated workbench exposes recording, inspect encoder capabilities
before connecting: PNG requires no encoder; GIF/MP4/WebM/WebP depend on detected
local encoder support. Missing support is unavailable, not permission to install
tools. Pause preview streaming before a bounded recording. Choose a new absolute
private output folder named `app-layer-INDEX-SUFFIX`, with an existing nonsymlink
parent outside the repository. Keep paths/receipts in the authenticated local
browser; public status must not disclose them. Stop the worker and release its
resources before teardown. Exports retain layer/source/time provenance and are
not atomic compositor, panel or AVPlay recordings. Recording/device acceptance
must be established separately; a host encoder check proves only host capability.

## Troubleshooting without bypasses

| Symptom | Check / stop condition |
|---|---|
| Missing CLI or extension | Owner-approved SDK setup; no automatic installation |
| SDB cannot connect while TCP works on Mac | Launching app's Local Network permission and actual Host PC route; no shared-server kill or bypass relay |
| `install failed[118]` | Target install permission; also DUID/profile/expiry/identity. Upstream permit fixed one set, not every failure |
| Signing/package failure | Samsung TV profile selected, author/distributor valid, target DUID covered, source/ID consistent; keep error details private |
| Update refused | Same author and IDs, intended version and target; never uninstall automatically to hide identity failure |
| Debug `closed`, no pages or port | Stop first, relevant legacy trailing `0`, fresh port, forward mapping and competing inspector ownership |
| `SecurityError` in ProductInfo/input | Required privilege and API loading/registration, not a hardware-support conclusion |
| Busy lane or cleanup mapping changed | Do not break/remove; coordinate owner and report blocked cleanup |

Source-backed observations: [lessons](lessons.md) and
[upstream walkthrough](https://github.com/dragoshont/plx-native/blob/dragoshont-samsung-tizen-feasibility/docs/samsung-getting-started.md).
