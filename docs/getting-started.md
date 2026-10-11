# Getting started: from zero to your app on a Samsung TV

This is the one guide for a new user. It uses the `tv` commands and the web portal
(the Samsung TV Developer Companion); both do the same thing with the same words.
Every command below is complete: copy it as is, replacing only `NAME` (the TV's name
as printed by `scan`, in quotes if it has spaces) and `APP` (an app ID such as
`AbCdEf1234.MyTv`).

## 1. What you need

- **Python 3.10 or newer.** Check with `python3 --version` (Windows: `py -3 --version`).
- **Tizen Studio** with the Web CLI and the Samsung TV and Samsung Certificate
  extensions, from [Samsung's SDK instructions](https://developer.samsung.com/smarttv/develop/getting-started/setting-up-sdk/installing-tv-sdk.html).
  The companion uses its `sdb` and `tizen` tools; see [prerequisites](prerequisites.md).
- **openssl** for the one-click certificate (built into macOS; on Windows it comes with Git for Windows).
- A Samsung account, for the certificate sign-in.
- The TV and this computer on the same network.

## 2. Get the companion

```sh
gh repo clone dragoshont/tizen-tv-builder
cd tizen-tv-builder
git checkout dragoshont-samsung-tv-remote-toolkit
python3 tools/tizen.py tv setup
```

`tv setup` checks Python, `sdb`, the `tizen` CLI, openssl and your signing profiles. It
installs nothing. On Windows write `py -3 tools\tizen.py` instead of `python3 tools/tizen.py`
in every command.

## 3. Find your TV

```sh
python3 tools/tizen.py scan
```

This lists the Samsung TVs on your network with their name, model, Tizen version and
status. A TV that is switched off stays listed as "Not reachable"; turn it on and scan again.

## New-TV onboarding

Turn on Developer Mode once per TV:

1. On the TV open **Apps**, then type **1 2 3 4 5** on the remote.
2. Switch **Developer mode** to **On**.
3. Set **Host PC IP** to this computer's address (`python3 tools/tizen.py info NAME`
   shows it as "this computer's IP for the TV").
4. Restart the TV (hold the power button until it turns off and on again).

Scan again: the TV now shows "Developer Mode ready". If it says "Not ready", Developer Mode
is on but the Host PC IP points to another computer.

## 4. Allow installing your apps on this TV

```sh
python3 tools/tizen.py tv cert NAME
python3 tools/tizen.py tv cert add NAME
```

`tv cert NAME` says whether your certificate already allows this TV. If it doesn't,
`tv cert add NAME` creates or renews it: Samsung sign-in opens in your browser, and that
is the only thing you do. There is no profile name to choose and no password to type; the
companion makes the profile `tv-companion`, keeps a backup of your previous files and makes
it the one used for installs. On macOS the password goes to the login Keychain, as Tizen
Studio does it. If your apps were signed with a different author certificate, the result
says they need to be reinstalled. Details: [live canvas](live-canvas.md).

## 5. Install, open and check your app

```sh
python3 tools/tizen.py install /path/to/MyApp.wgt NAME
python3 tools/tizen.py install https://example.com/builds/MyApp.wgt NAME
python3 tools/tizen.py apps NAME
python3 tools/tizen.py info NAME --app APP
python3 tools/tizen.py open NAME APP
python3 tools/tizen.py screenshot NAME --app APP --out ~/Pictures/TV-Screenshots
python3 tools/tizen.py info NAME --connect
python3 tools/tizen.py tv uninstall NAME APP
```

NAME is the TV's IP address, its name as `scan` shows it, or its `tv-xxxxxx` alias.

- `install` signs a private copy with your active certificate, installs it, and only
  says "Installed" when the TV's own app list shows the app. An `https://` link is
  downloaded to a private folder (at most 256 MB and 5 minutes), checked to be a Tizen
  app package, installed and then deleted; only the link without its query or password
  is printed. Plain `http://` is refused.
- `apps` lists every app ID on the TV.
- `info NAME --app APP` answers installed / not installed / unknown for one app ID or
  app name. The version is printed only when the TV's own app list shows one; the 2017
  and 2019 TVs list IDs only, so it says "Version: unknown". The version inside a
  downloaded package is not proof of what is installed.
- `open` restarts the app in debug mode so its screen can be seen.
- `screenshot` saves the app's picture as a PNG (default folder `~/Pictures/TV Screenshots`).
- `info --connect` shows every detail the portal shows, including the Tizen version,
  certificate coverage and apps.
- `tv uninstall` removes the app and checks the app list again. Some TVs (Tizen 5.0) report
  success without removing it; then the command tells you to delete it on the TV.

Every other TV command (`setup`, `cert`, `uninstall`, `key`, ...) stays under
`python3 tools/tizen.py tv COMMAND`, and `python3 tools/tizen.py tv install NAME APP.wgt`
(TV first) still works. The older sdb form `python3 tools/tizen.py install --serial SERIAL APP.wgt`
is unchanged.

Each command connects, does its job and disconnects. The TV's real error is printed
when something fails, with the next command to run.

## The same steps in the web portal

```sh
python3 tools/tizen.py portal
```

Open the printed `http://127.0.0.1:47811/`. Ctrl+C stops it; `--port N` picks another port.

1. The portal scans on its own. **Scan network for TVs** scans again.
2. Pick your TV and choose **Connect**.
3. If the card says "You can't install your apps on this TV yet", choose
   **Allow installing my apps on this TV** and sign in to Samsung in your browser.
4. **Choose .wgt…**, then **Install**.
5. Choose **Open screen** on your app: the picture appears with **Refresh**,
   **Save screenshot** and the remote.
6. **TV details** shows everything `tv info` shows; **Uninstall** is next to each app
   you installed from here.

The portal needs macOS or Linux: its private profile relies on POSIX file permissions.
On Windows use the `tv` commands, or the [TV Workbench](workbench.md).

## Debug and private viewer

**Open screen** (or `tv open`) stops the app, launches it in debug mode, forwards its
inspector port to this computer only and shows the app's picture. The picture is the web
page as the browser engine paints it, not the TV panel: video playing through AVPlay does
not appear, and neither does anything drawn by the TV itself. The viewer page has a
private address on `127.0.0.1`; do not share it. The [lessons](lessons.md) explain the
Tizen 3.0 inspector details.

## Make your own app

```sh
python3 tools/tizen.py new ./MyTv --name "My TV"
python3 tools/tizen.py validate MyTv/app
python3 tools/tizen.py audit MyTv/app
python3 tools/tizen.py pack MyTv/app --output MyTv.wgt
python3 tools/tizen.py check-package MyTv.wgt
python3 tools/tizen.py install MyTv.wgt NAME
```

- `new` creates a starter app with a unique ten-character package ID, an ES5 template
  that runs on old TVs, and host tests (`node --test MyTv/tests/focus.test.js`).
- `validate` and `audit` check the IDs, privileges, ES5 syntax and leaked credentials.
- `pack` makes an unsigned `.wgt`; `tv install` signs it with your certificate.
- To try the flow first, install the sample: `python3 tools/tizen.py pack examples/hello-tv --output HelloTv.wgt`.

More checks (budgets, retail runs, the emulator): [app flows](app-flows.md).
Old-engine pitfalls: [lessons](lessons.md). What each TV year supports: [compatibility](compatibility.md).

## What has been checked

On a 2017 (Tizen 3.0) and a 2019 (Tizen 5.0) TV: scan, connect, app list, install,
**Open screen**, the remote and screenshots. `tv cert add` is tested against a fake
Samsung service only, not yet against Samsung's live one. Other TV years are unverified;
a working command on one TV does not prove another model.

## When something fails

| You see | Do this |
|---|---|
| "Not reachable" | Turn the TV on, then scan again |
| "Not ready" | Set the TV's Host PC IP to this computer, restart the TV |
| "You can't install your apps on this TV yet" | `python3 tools/tizen.py tv cert add NAME` |
| `install failed[118]` | The certificate doesn't list this TV: `tv cert add NAME` |
| "No picture" after Open screen | Choose Open screen again; the app restarts |
| sdb can't connect on a Mac | Allow Local Network access for your terminal in System Settings → Privacy & Security |

Keep certificates, passwords and device IDs out of git and public logs.
