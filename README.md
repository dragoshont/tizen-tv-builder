# Tizen TV Builder

Build a Samsung TV app without relearning the same lessons every time.

This is a small toolkit for the things that slow you down: old browser engines,
certificates, packaging, deployment and debugging. It comes with a coding agent
that knows the workflow and the mistakes we have already found.

Use it for a Plex client, an Immich app or your own project. It is not another
media client. It does not promise that one app will work on every TV from 2015
to today. We keep [the model-year table](docs/compatibility.md), what was actually
tested, and what is still unknown.

**Start a new app:** follow [prerequisites](docs/prerequisites.md), then
[the signed-app getting-started guide](docs/getting-started.md). Humans and
agents use the same commands and checks; [AGENTS.md](AGENTS.md) indexes the
focused workflows and lessons.

The checkout includes focused agent skills for
[device leases](skills/samsung-tv-lease/SKILL.md),
[connecting a new TV](skills/samsung-tv-connect/SKILL.md),
[debug launch](skills/samsung-tv-debug/SKILL.md),
[inspection and input](skills/samsung-tv-inspect/SKILL.md),
[certificates/signing](skills/samsung-tv-certificates/SKILL.md) and
[starting an app](skills/samsung-tv-new-app/SKILL.md).
These additions are unreleased; the published 0.1.0 plugin is unchanged.

## Install the plugin

You need a current **GitHub Copilot CLI**. The plugin uses the documented
[Agent Plugins 1.0 format](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/plugins-creating):
a portable skill, plus a Copilot-specific agent.

Recommended: install the **0.1.0 release** through its marketplace:

```sh
copilot plugin marketplace add dragoshont/tizen-tv-builder#v0.1.0
copilot plugin install tizen-tv-builder@tizen-tv-tools
copilot plugin list
copilot skill list
```

This repository is currently **private**. You need repository access and working
GitHub authentication. The marketplace is pinned to the release tag. It installs this plugin, not Python,
Tizen Studio, a Samsung certificate or a TV app.
No hooks, MCP servers or background services are bundled.

That describes the published **0.1.0** package. The checkout now also contains an
**unreleased live-canvas candidate**, held for parent/device acceptance. Do not
replace your installed plugin or publish it yet. See [live canvas setup](docs/live-canvas.md)
for the standalone adapter, explicit private profile and SDK opening steps.
It also adds an unreleased `tools/tv.py` CLI (scan, rename, connect, key, screenshot,
portal) with the same capabilities as the unauthenticated loopback portal.

For a fixed version, download the release instead:

```sh
mkdir tizen-builder-0.1.0
cd tizen-builder-0.1.0
gh release download v0.1.0 --repo dragoshont/tizen-tv-builder \
  --pattern 'tizen-tv-builder-0.1.0.zip' --pattern SHA256SUMS --pattern release.json
shasum -a 256 -c SHA256SUMS
unzip tizen-tv-builder-0.1.0.zip
copilot plugin marketplace add ./tizen-tv-builder
copilot plugin install tizen-tv-builder@tizen-tv-tools
copilot plugin list
```

On Linux, `sha256sum -c SHA256SUMS` is the equivalent checksum command.
Checksums catch mismatches; they are not an independent publisher signature.
`release.json` records the source commit and every packaged file's checksum.
The extracted release becomes a local marketplace, so keep that directory.
Local-marketplace plugins load from it; do not edit it if you want a fixed version.
Use a fresh session or the host's supported reload/restart to discover new components.

Do not add both marketplace sources at once: they use the same marketplace name.
For a new release, uninstall only this plugin, remove `tizen-tv-tools`, then add the
new release tag/directory and install it again. Do not update all your other plugins.
Direct `copilot plugin install OWNER/REPO` works in the tested CLI but is deprecated;
the marketplace path is the recommended one.

## Use it in your app

In Copilot, select **tizen-tv-builder** from `/agent`, or keep your current agent
and invoke the **tizen-tv-builder** skill. Start with something concrete:

> Use tizen-tv-builder. Check this app for a 2017 Samsung TV running Tizen 3.0.
> Read the code, run the host checks and make a short plan. Do not touch the TV.

The skill finds the installed toolkit and reads its lessons. It uses your app's
source, not the bundled hello example. It should tell you what passed, what failed
and what needs a real TV. Installing the agent does not grant it device access.

Already working in another session? Load the skill there. You do not need another
supervisor or a new project just to use these tools.

## Use the tools yourself

For a standalone remote, inspector and native paint preview, run
`python3 tools/workbench.py` on macOS or `py -3 tools/workbench.py` on Windows.
**TV Workbench** opens in your browser without Plex, Copilot or npm. Its
first-run flow checks installed tools and guides explicit TV/app debug setup.
It detects connected TVs and installed apps, and shows a complete Samsung-style
remote with app-supported keys. See [workbench setup](docs/workbench.md):
this is unreleased. Hosted Windows checks passed; desktop Windows and TV
acceptance remain pending.

**Record** saves one genuine app paint layer as PNG frames or a still.
An already-installed optional ffmpeg enables optimized GIF, MP4 and WebM
exports—no encoder is bundled or silently installed. FPS, size, length and
the private output folder are in **Recording & export**.
See [recording formats and setup](docs/recording.md).

**New app in one command:** `python3 tools/tizen.py new ./MyTv --name "My TV"`, then
`audit`, `pack` and `check-package` without an SDK. Retail TV and emulator runs with
per-tier start-up budgets: see [app flows](docs/app-flows.md).

The plugin includes the Python tools, docs and a small hello app. Run the commands
below from an extracted release or a repository checkout. To get a checkout:

```sh
gh repo clone dragoshont/tizen-tv-builder
cd tizen-tv-builder
python3 tools/tizen.py --version
python3 tools/tizen.py doctor
python3 tools/tizen.py validate examples/hello-tv
python3 tools/tizen.py build examples/hello-tv
```

You need **Python 3.10+**. SDK commands need an already-installed Tizen Studio
with Web CLI and the Samsung TV Extension. Get them from
[Samsung's SDK instructions](https://developer.samsung.com/smarttv/develop/getting-started/setting-up-sdk/installing-tv-sdk.html),
with the computer owner's permission. `doctor` checks local tools; it does not
connect to a TV. Set `TIZEN_SDK_ROOT` or pass `--tizen PATH` / `--sdb PATH` if needed.

`build` is an unsigned source build in a temporary directory. It does not create
a deployable signed package or prove that the app works on a TV. SDK wrappers
are **macOS/Linux only**: timeout, Ctrl-C or SIGTERM stops their owned process
group, not a shared SDB server. On Windows, use Samsung's CLI directly;
manifest validation and HTTP inspector discovery do not launch SDK processes.

## First install on a TV

First identify the exact model, region, firmware and year. Enable Developer Mode
using [Samsung's device instructions](https://developer.samsung.com/smarttv/develop/getting-started/using-sdk/tv-device.html).
Connect with Device Manager; this toolkit does not connect for you.

Create a **Samsung TV** certificate profile in Certificate Manager: author
certificate, distributor certificate, and the target TV's DUID. Then use Device
Manager's **Permit to install applications** for that TV. Follow
[Samsung's certificate guide](https://developer.samsung.com/smarttv/develop/getting-started/setting-up-sdk/creating-certificates.html).
Keep certificates, passwords and DUIDs private.

With permission for the exact TV and these operations:

```sh
python3 tools/tizen.py package /path/to/app --profile YOUR_PROFILE --output /private/path/app.wgt
python3 tools/tizen.py install /private/path/app.wgt --serial TV_IPV4:26101 --authorized
python3 tools/tizen.py run --app PACKAGEID0.Application --serial TV_IPV4:26101 --authorized
```

Replace the placeholders. Package IDs must be ten letters/digits; use the app ID
from `config.xml`. Only one worker should own that Samsung TV. `--authorized`
acknowledges permission; it is not a lock and cannot prove consent.

Packaging stages common web assets and excludes known private/generated paths.
It is **not a secret scanner**. A token in a JS file is still a token. Review what
you ship. A requested signing profile is not cryptographic signature verification,
Samsung provenance verification or Store approval.

## Debug without promising a recording

```sh
python3 tools/tizen.py debug --app PACKAGEID0.Application --serial TV_IPV4:26101 --authorized
# For the observed legacy 2017 workaround, add --legacy-timeout 0.
# Read the returned port, then:
python3 tools/tizen.py forward --port DEBUG_PORT --serial TV_IPV4:26101 --authorized
python3 tools/tizen.py inspect --port DEBUG_PORT
# In your cleanup/finally block, remove only the forward you created:
python3 tools/tizen.py unforward --port DEBUG_PORT --serial TV_IPV4:26101 --authorized
```

`inspect` reads loopback HTTP metadata only. Each of its two requests has a total
five-second deadline and a one-MiB body limit. It reports engine/protocol and
target types, not page titles, URLs, DOM or account data. It is not a WebSocket
client or an image-capture tool.

On the studied 2017 TV, `Page.captureScreenshot` timed out and screencasting
reported false. Newer investigation reports native **paint-layer snapshots**;
those are not panel/compositor/video-plane captures or a recording. Read
[the dated capture lesson](docs/lessons.md) before generalizing.

The unreleased candidate adds successive native paint-layer frames, user-operated
remote controls and source metadata in a standalone Copilot canvas. It does not
claim TV acceptance from host fixtures. The builder's live UI acceptance workflow
requires this canvas when available, or an explicit unavailable/blocked result.

## What we know, and what we do not

- [Compatibility](docs/compatibility.md): annual engines, capability boundaries and TV acceptance.
- [Lessons](docs/lessons.md): signing, host issues, inspector, media, networking and UI mistakes.
- [Experience review](docs/experience-review.md): what we captured, what is implemented and what is still missing.
- [Changelog](CHANGELOG.md): version changes. No version number means all TVs are supported.

Run the host suite with `python3 -m unittest discover -s tests -v`.
We have exercised offline tests, a real unsigned SDK build, hosted Windows
checks and [bounded retail paint recording](docs/workbench.md#bounded-retail-observation-2026-10-11).
Full retail install/input/media acceptance remains incomplete. Emulator and desktop results are
not TV results. Native binaries, root/firmware work, DRM bypass and automatic
account writes are out of scope.

To build a release, update `plugin.json`, `.github/plugin/marketplace.json` and
`CHANGELOG.md` together, run the suite, and commit a clean tree. Then run:

```sh
python3 tools/release.py --output /absolute/existing/release-directory
```

The builder refuses a dirty tree, unknown tracked files, symlinks and existing
output files. It makes a deterministic ZIP, `release.json` and `SHA256SUMS`.
The output directory must be outside the repository. Review those artifacts
before tagging/publishing; the builder does not push or install anything.

MIT, independently written. No Samsung SDK binaries or GPL client/inspector code
are bundled. Factual lessons credit the
[PlxNative investigation](https://github.com/dragoshont/plx-native).
