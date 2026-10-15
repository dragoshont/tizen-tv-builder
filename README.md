# Tizen TV Builder

Build a Samsung TV app without relearning the same lessons every time.

## Start the Samsung TV Developer Companion

```
python3 tools/tizen.py portal
```

- It prints `http://127.0.0.1:47811/`; open that in your browser. Ctrl+C stops it.
  Run the same command again to restart. `--port N` picks another port.
- Requirements: Python 3.10+, Tizen Studio's `sdb` and `tizen` CLI.
- On the TV: Apps → 1 2 3 4 5 → Developer mode On → Host PC IP = this computer →
  restart the TV.
- Then: Scan → pick a TV → Connect → Open screen on your app.

**New here?** Four steps: clone this repository, run `python3 tools/tizen.py setup` (prepares
this computer, installing Tizen Studio's CLI if missing), turn on Developer Mode on the TV, then
`python3 tools/tizen.py scan`, `python3 tools/tizen.py cert add TV` (one Samsung
sign-in when certificate coverage is needed), and `python3 tools/tizen.py install APP.wgt TV`.
[Getting started](docs/getting-started.md) is the one from-zero guide: TV
Developer Mode, the one-click certificate, install, open, screenshot and uninstall, as
`tv` commands and as portal steps, plus making your own app.

This is a small toolkit for the things that slow you down: old browser engines,
certificates, packaging, deployment and debugging. It comes with a coding agent
that knows the workflow and the mistakes we have already found.

Use it for a Plex client, an Immich app or your own project. It is not another
media client. It does not promise that one app will work on every TV from 2015
to today. We keep [the model-year table](docs/compatibility.md), what was actually
tested, and what is still unknown.

**Start a new app:** follow [getting started](docs/getting-started.md)
([prerequisites](docs/prerequisites.md) has the SDK details). Humans and
agents use the same commands and checks; [AGENTS.md](AGENTS.md) indexes the
focused workflows and lessons.

The checkout includes focused agent skills for
[device leases](skills/samsung-tv-lease/SKILL.md),
[connecting a new TV](skills/samsung-tv-connect/SKILL.md),
[debug launch](skills/samsung-tv-debug/SKILL.md),
[inspection and input](skills/samsung-tv-inspect/SKILL.md),
[certificates/signing](skills/samsung-tv-certificates/SKILL.md) and
[starting an app](skills/samsung-tv-new-app/SKILL.md).
These commands and focused skills are available in the main checkout. The
versioned 0.1.0 plugin is an older snapshot; it does not include the current portal.

## Scope and current limits

The Python commands work without an AI agent or Copilot. Start with the checkout and
[getting started](docs/getting-started.md), not the older plugin, for the current
scan/certificate/install/remote workflow.

On macOS, this workflow has been used with Plx on Samsung MU6172 (2017), RU7092
(2019), Q80C (2023) and QN70F (2025) sets. That is evidence for those operations,
not certification of every model or app. Windows desktop-to-TV operation and
2015/2016 retail hardware remain unverified.

Capture shows the app's browser drawing, not the panel or AVPlay video. It can fail
when the app has more than eight paint layers, or when an inspector uses unsupported
WebSocket behavior. Debug/capture relaunches the app. A successful command is not proof
of physical-remote fidelity, codec support or Store approval.

The latest parent-reported Vemm 0.0.1 installation/runtime smoke used source
`2b50628cd9a346dbe1d374e103124665b749b260` on QE65Q80CATXXH (2023), whose
observed runtime was **Tizen 9.0**, not the model year's 7.0 baseline. Identity,
profile-picker state and 1920x1080 geometry were checked without remote/media/account
operations or capture. An earlier deployment's inspector rejected
`LayerTree.makeSnapshot`; this is not a screenshot or physical-panel acceptance.
Use `python3 tools/tizen.py tv launch TV APP` to
start an app without requiring capture. See [the dated lesson](docs/lessons.md#vemm-deployment-observation-15-october-2026).

The portal runs on loopback without a token. Its browser Host/Origin checks reject
other websites, and anti-framing headers prevent websites from embedding its controls.
Local programs and other users on this computer can call it while
it runs. Use it on a trusted single-user computer and stop it when done. The Workbench
is a separate tool with a bearer token.

Open the portal as a top-level browser page. Hosts that try to embed the optional
canvas in an iframe will be blocked by this policy; no arbitrary frame-origin
exception is enabled to make embedding work.

There is no live `logs`/`debugger-tail` command yet; it is tracked in
[issue #1](https://github.com/dragoshont/tizen-tv-builder/issues/1).
Performance budgets consume measured timings; they do not measure presented TV FPS.

The checkout also includes a [bounded OFF/ON ABBA benchmark](docs/benchmark.md):
`python3 tools/tizen.py benchmark run` attaches to an explicitly reviewed,
account-free app adapter, and `benchmark validate` / `benchmark summarize` work
offline. Raw samples, warmups and provenance are retained; callback intervals
are not panel FPS and CPU/API timings are not GPU kernel time. No automatic
deployment or production-account work is included.

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

The marketplace is pinned to the release tag. It installs this plugin, not Python,
Tizen Studio, a Samsung certificate or a TV app.
No hooks, MCP servers or background services are bundled.

That describes the published **0.1.0** package. The main checkout also contains a
standalone live canvas and the current Python TV commands. See
[live canvas setup](docs/live-canvas.md) for the adapter, private profile and SDK opening
steps. Plugin installation alone does not register a canvas extension.
Current TV commands include `python3 tools/tizen.py tv scan`,
`tv list`, `tv check TV`, `tv connect TV`, `tv info TV` (the portal's detail panel), first-run `tv setup`, `tv cert`, `tv install`,
`tv launch`, `tv remove` and `tv portal` (the same features in a
loopback web page). See [live canvas setup](docs/live-canvas.md#same-features-from-the-terminal-tizenpy-tv).

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
git clone https://github.com/dragoshont/tizen-tv-builder.git
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

Follow [getting started](docs/getting-started.md): `python3 tools/tizen.py scan`, TV
Developer Mode, `python3 tools/tizen.py tv cert add NAME` (one Samsung sign-in in your
browser, no other prompts), then `python3 tools/tizen.py install APP.wgt NAME` (a file or an
`https://` link). Then `python3 tools/tizen.py apps NAME`, `python3 tools/tizen.py info NAME --app APP`,
`python3 tools/tizen.py open NAME APP` and `python3 tools/tizen.py screenshot NAME --app APP`. Keep
certificates, passwords and device IDs private.

The lower-level `package`, `install` and `run` commands remain for agents and scripted
acceptance runs; they need `--serial` and `--authorized`, see [app flows](docs/app-flows.md).

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

Until a new version is explicitly approved, a build from current main is a
**distribution candidate**, not the historical 0.1.0 release. Both still report
0.1.0; distinguish them by `release.json`'s `source_commit` and archive checksum.
Do not overwrite published assets, install the candidate over a pinned plugin or
announce a new version from this filename alone.

The builder refuses a dirty tree, unknown tracked files, symlinks and existing
output files. It makes a deterministic ZIP, `release.json` and `SHA256SUMS`.
The output directory must be outside the repository. Review those artifacts
before tagging/publishing; the builder does not push or install anything.

MIT, independently written. No Samsung SDK binaries or GPL client/inspector code
are bundled. Factual lessons credit the
[PlxNative investigation](https://github.com/dragoshont/plx-native).
