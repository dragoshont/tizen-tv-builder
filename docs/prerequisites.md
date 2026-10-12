# Prerequisites: macOS and Windows

Use this with [zero to a signed app](getting-started.md). The plugin provides
guidance and Python tools, not an SDK, certificate, Samsung account or TV.
The published plugin remains **0.1.0**; the standalone workbench and focused
skills in this checkout are unreleased.

| Capability | Required | What remains unproven |
|---|---|---|
| Validate a manifest | Python 3.10+ | Runtime/TV compatibility |
| Build and sign | Installed Tizen Studio Web CLI, TV Extension, Samsung certificate profile | Install, cryptographic verification, Store approval |
| Connect/install/debug | Developer Mode, correct Host PC IP, installed SDB, target grant and free device lane | App behavior on another model |
| Private viewer/remote | Unreleased workbench, debug-enabled app, owned forward, separate pixel/input grants | Panel/video-plane capture; Windows/device acceptance |
| Discover a new TV | Current workbench discovery capability and scoped network authorization | Identity or metadata not returned by the device |

## macOS: check before installing

1. Check Python in Terminal:
   ```sh
   python3 --version
   ```
   Expect `Python 3.10.x` or newer. If absent/older, have the computer owner
   install a supported Python; do not install automatically.
2. With owner approval, install the SDK from
   [Samsung's TV SDK instructions](https://developer.samsung.com/smarttv/develop/getting-started/setting-up-sdk/installing-tv-sdk.html).
   In Package Manager select the **Web CLI**, **Samsung TV Extension** and
   **Samsung Certificate Extension** needed by Certificate Manager. Names and
   availability can change; follow the installed SDK's current instructions.
3. Check the installed executables (replace paths if installed elsewhere):
   ```sh
   "$HOME/tizen-studio/tools/ide/bin/tizen" version
   "$HOME/tizen-studio/tools/sdb" version
   python3 tools/tizen.py --tizen "$HOME/tizen-studio/tools/ide/bin/tizen" \
     --sdb "$HOME/tizen-studio/tools/sdb" doctor
   ```
   Expect version text and doctor JSON with both `available: true` and
   `"device": "not contacted"`. Doctor can exit successfully with a missing
   tool: check those fields, not just exit status. It does not confirm installed
   extension completeness. Verify TV/certificate extensions in Package Manager.
4. Allow **the launching application** normal Local Network access in System
   Settings → Privacy & Security. Confirm the active route/interface when
   entering the TV's Host PC IP. A successful Apple TCP probe alone does not
   establish SDB permission. No privacy-bypass relay is part of this guide.

SDK command-line readiness does not establish emulator readiness. The upstream
x86 TV image did not run on Apple Silicon. Turning GPU acceleration off does
not supply Intel CPU virtualization. Recheck actual shipped host/image support
before an approved upgrade; no emulator installation is required for this guide.

## Windows: PowerShell, not POSIX wrappers

1. Check the installed launcher:
   ```powershell
   py -3 --version
   ```
   Expect Python 3.10+. If `py` is absent but `python` is installed, verify
   `python --version` and use it consistently instead.
2. With computer-owner approval, install Tizen Studio, Web CLI, Samsung TV and
   Certificate extensions using the same vendor instructions. A typical SDK
   location is `C:\tizen-studio`; use the actual location:
   ```powershell
   $Tizen = 'C:\tizen-studio\tools\ide\bin\tizen.bat'
   $Sdb = 'C:\tizen-studio\tools\sdb.exe'
   & $Tizen version
   if ($LASTEXITCODE -ne 0) { throw 'Tizen CLI check failed' }
   & $Sdb version
   if ($LASTEXITCODE -ne 0) { throw 'SDB check failed' }
   py -3 tools/tizen.py validate examples/hello-tv
   ```
   Expect tool versions and validation JSON naming `PlxHelloTv.Hello`.
   Use Device Manager to confirm extension readiness. Do not run `doctor`,
   `build`, `package`, `install`, `run`, `debug` or forward SDK wrappers here:
   `tools/tizen.py` SDK process management is POSIX-only.
3. Resolve Windows firewall/network access normally with the owner; never
   disable security protections as an emulator/connectivity recipe.
4. The standalone command `py -3 tools/workbench.py --sdb $Sdb` supports a
   Windows executable path. Hosted Windows Server loopback and native private
   recording checks passed; desktop Windows 10/11 and a real Windows-to-TV run
   remain unverified. See [the dated CI receipt](workbench.md).

Windows x64/Ryzen is not an emulator pass. Samsung's upstream troubleshooting
lead uses Windows Hypervisor Platform, not retired Intel-only HAXM as an AMD
fix. Confirm current SDK/image documentation before any host change. A desktop
browser or emulator never certifies the named retail TV.

## Before a device run

- Record exact model/group/region, firmware and source SHA privately. Unknown
  model year stays unknown; an engine string is not hardware identification.
- Use a permitted LAN without client isolation. Enter the actual Host PC IP,
  not a guessed address from an inactive interface.
- Obtain the target/operation scope once; reuse that valid scope without asking
  for every button. Additional account writes, installation, pixels or app
  actions outside it need their own grant. Never break a live lock.
- Coordinate one physical-TV owner across hosts. Build without a lease; use
  the existing canonical Samsung lane for one bounded run.
- Keep certificates/DUIDs/serials and private terminal output outside git.
  No root, general retail shell, firmware modification or TLS bypass.

Sources: [upstream getting started](https://github.com/dragoshont/plx-native/blob/84712aa54a687e323bc00de46713bd978c94732e/docs/samsung-getting-started.md),
[Samsung TV connection](https://developer.samsung.com/smarttv/develop/getting-started/using-sdk/tv-device.html),
[compatibility matrix](compatibility.md). Vendor instructions win when changed.
