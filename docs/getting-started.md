# Getting started: from zero to your app on a Samsung TV

This is the one guide for a new user. It uses the `tv` commands and the web portal
(the Samsung TV Developer Companion); both do the same thing with the same words.
Every command below is complete: copy it as is, replacing only `NAME` (the TV's name
as printed by `scan`, in quotes if it has spaces) and `APP` (an app ID such as
`AbCdEf1234.MyTv`).

In short:

1. Clone this repository.
2. `python3 tools/tizen.py setup` prepares this computer.
3. Prepare the TV: turn on Developer Mode.
4. `python3 tools/tizen.py scan`, then `python3 tools/tizen.py install APP.wgt TV`.

## 1. What you need

- **Python 3.10 or newer.** Check with `python3 --version` (Windows: `py -3 --version`).
- **Tizen Studio's command-line tools** (`sdb` and `tizen`) with the Samsung TV and
  Samsung Certificate extensions. `python3 tools/tizen.py setup` installs them if they are
  missing; to do it by hand, follow [Samsung's SDK instructions](https://developer.samsung.com/smarttv/develop/getting-started/setting-up-sdk/installing-tv-sdk.html)
  and [prerequisites](prerequisites.md).
- **openssl** for the one-click certificate (built into macOS; on Windows it comes with Git for Windows).
- A Samsung account, for the certificate sign-in.
- The TV and this computer on the same network.

## 2. Get the companion and set up this computer

```sh
git clone https://github.com/dragoshont/tizen-tv-builder.git
cd tizen-tv-builder
python3 tools/tizen.py setup
```

`setup` checks Python, `sdb` and the `tizen` CLI. If `sdb` or `tizen` is missing it
downloads and runs Tizen Studio's command-line installer for your system, with progress,
then checks again. If that fails it prints the real error and the one command to run by
hand. It only prepares this computer: it does not contact a TV, make a certificate or wait
for anything, and it ends by printing the TV steps below with this computer's IP.
`python3 tools/tizen.py setup --check` only reports and installs nothing.

For a guided walk through every step, from this computer to your app on the TV, use
`python3 tools/tizen.py tv setup` instead.

On Windows write `py -3 tools\tizen.py` instead of `python3 tools/tizen.py` in every command.

## 3. Prepare your TV

Turn on Developer Mode once per TV:

1. On the TV open **Apps**, then type **1 2 3 4 5** on the remote.
2. Switch **Developer mode** to **On**.
3. Set **Host PC IP** to this computer's address (`python3 tools/tizen.py setup` prints it).
4. Restart the TV (hold the power button until it turns off and on again).

## 4. Find your TV

```sh
python3 tools/tizen.py scan
```

This lists Samsung TVs with their name, model and observed status. **Ready to connect**
means the developer port answers, not that the certificate or installation permission
has been checked. **Needs setup** means a setup step is needed; follow the reported
step rather than assuming the cause is Host PC IP. An unavailable remembered TV stays
listed as **Not reachable**. Turn it on and scan again.

An open or closed port does not prove whether Developer Mode is enabled. Tizen version
is observed on the developer connection; a model-year baseline is not the TV's current
OS version.

## 5. Allow installing your apps on your TVs

```sh
python3 tools/tizen.py tv cert NAME
python3 tools/tizen.py cert add NAME
python3 tools/tizen.py cert add NAME1 NAME2 NAME3 NAME4
```

`tv cert NAME` says whether your certificate already allows this TV. If it doesn't,
`cert add` creates or renews it: Samsung sign-in opens in your browser, and that is the
only thing you do. There is no profile name to choose and no password to type; the
companion makes the profile `tv-companion`, keeps a backup of your previous files and makes
it the one used for installs. On macOS the password goes to the login Keychain, as Tizen
Studio does it. If your apps were signed with a different author certificate, the result
says they need to be reinstalled. Details: [live canvas](live-canvas.md).

With several TVs, name them all: the companion first reads every TV's device ID (DUID)
and stops before any sign-in if one can't be read. Then **one** Samsung sign-in makes one
certificate for all of them, keeping the TVs your certificate already allowed and the same
author certificate. The result lists every TV as "can install your apps" or "not allowed
yet", and the command fails if any TV is not allowed. If every named TV is already
allowed, nothing opens and nothing changes. Ctrl+C cancels the sign-in; nothing is changed
and running the same command starts a new one.

### Starting over (optional)

To remove an app you installed before, and the old signing profiles, from a clean slate:

```sh
python3 tools/tizen.py remove NAME APP
python3 tools/tizen.py cert reset PROFILE [PROFILE...]
python3 tools/tizen.py cert reset PROFILE [PROFILE...] --yes
```

- `remove NAME APP` (also `uninstall`) removes exactly that app ID. It reads the TV's app
  list first: not installed means nothing to do (success, nothing sent); an unreadable list
  means it stops before uninstalling. After uninstalling it reads the list again and says
  "removed" only when the app is gone; still listed or unreadable is a failure. It prints a
  one-line receipt. It never removes other apps.
- `cert reset PROFILE...` without `--yes` only shows what would be removed and changes
  nothing. With `--yes` it removes exactly those signing profiles: their `profiles.xml`
  entries, their folder under `~/SamsungCertificate`, and (on macOS) their Keychain
  passwords. A backup and a `receipt.json` are written to the companion's state folder
  first. A missing profile is nothing to do. It refuses, changing nothing, for symbolic
  links, unsafe names, folders outside `~/SamsungCertificate`, or a folder that another
  profile still uses. Files a profile uses from elsewhere are kept and listed. After a
  reset, `cert add` makes a new author certificate, so apps signed before must be
  reinstalled.

## 6. Install, open and check your app

```sh
python3 tools/tizen.py install /path/to/MyApp.wgt NAME
python3 tools/tizen.py install https://example.com/builds/MyApp.wgt NAME
python3 tools/tizen.py apps NAME
python3 tools/tizen.py info NAME --app APP
python3 tools/tizen.py open NAME APP
python3 tools/tizen.py screenshot NAME --app APP --out ~/Pictures/TV-Screenshots
python3 tools/tizen.py info NAME --connect
python3 tools/tizen.py remove NAME APP
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
- `install` again with the same package is safe. "Installed" means the app is listed; a
  version is shown only when the TV lists one, so an app that was already there is not proof
  that the new version replaced it.
- `remove` removes the app and checks the app list again (see above). Some TVs (Tizen 5.0)
  report success without removing it; then the command says it is still installed.

Every other TV command (`setup`, `key`, ...) stays under
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

The main checkout has been used on macOS for scan/connect, app listing, signing and
installation of Plx on MU6172 (2017), RU7092 (2019), Q80C (2023) and QN70F (2025)
sets. Live Samsung sign-in and batch `cert add` completed on 12 October 2026.
Remote navigation and renderer captures were exercised on named sets, not every
possible model/operation combination. Some captures fail because of layer limits
or inspector protocol behavior; they are not full-panel/video-plane captures.

This is not universal app or firmware acceptance. No new Windows-to-retail-TV,
2015/2016 hardware or emulator acceptance is claimed.

## When something fails

| You see | Do this |
|---|---|
| "Not reachable" | Turn the TV on, then scan again |
| "Needs setup" | Follow the specific setup step shown; check Developer Mode and Host PC IP when indicated |
| "You can't install your apps on this TV yet" | `python3 tools/tizen.py tv cert add NAME` |
| `install failed[118]` | Check both certificate coverage and installation permission; this error alone does not establish which failed |
| "No picture" after Open screen | Choose Open screen again; the app restarts |
| sdb can't connect on a Mac | Allow Local Network access for your terminal in System Settings → Privacy & Security |

Keep certificates, passwords and device IDs out of git and public logs.
