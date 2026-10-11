---
name: tizen-tv-builder
description: Build, package, sign, debug or review Samsung Tizen TV web apps. Use for SDK readiness, 2015-current model compatibility, AVPlay/MSE, certificates, Web Inspector, safe media-client networking and model-bound acceptance. Useful for Plex, Immich and other backends; not a native/root deployment or a blanket TV support claim.
---

## Find the toolkit, not the current app

Resolve the toolkit root as `../..` from this skill's host-reported base directory.
Read README.md, docs/compatibility.md, docs/lessons.md and docs/experience-review.md there.
Do not assume the app's current working directory is the plugin. If the host does not
report a skill base path, use `copilot skill list --json` or request the installed path;
never guess a cache directory. Do not overwrite the app's own working policy.

Use `python3 <toolkit-root>/tools/tizen.py --version` and `--help`.
Run doctor before assuming SDK availability. This reads installed SDK versions only;
it does not connect to a TV. Python 3.10+ is required. SDK wrappers are POSIX-only.
Pass the actual app source path to validate/build/package, not this plugin's example.
No SDK installation, account sign-in or device access is implied by loading this skill.

## Work on the application

For a first account-free app, read [prerequisites](../../docs/prerequisites.md) and
[zero to signed app](../../docs/getting-started.md). Use the narrow checkout skills
when appropriate: [lease](../samsung-tv-lease/SKILL.md),
[connection/new TV](../samsung-tv-connect/SKILL.md),
[kill-before-debug](../samsung-tv-debug/SKILL.md),
[inspector/viewer/remote](../samsung-tv-inspect/SKILL.md),
[certificates](../samsung-tv-certificates/SKILL.md) and
[new app/sign/install/verify](../samsung-tv-new-app/SKILL.md).
They use normal plugin skill discovery, not extra hooks or a separate host skills
directory. These additions are unreleased; published 0.1.0 remains unchanged.

Workflow: inspect named model and official engine/API tables -> validate manifest -> host tests
and unsigned source build -> certificate profile -> explicit authorized install -> named-model
remote/media/lifecycle acceptance -> clean teardown and evidence receipt.

Do not install SDKs automatically. The toolkit wraps an already-installed Tizen CLI/SDB.
Unsigned build is not a signed developer package. A Samsung distributor certificate must include
the target DUID; certificate values remain private. Package ID is ten alphanumeric characters.
The agent prompt is `com.github.copilot/agents/tizen-tv-builder.agent.md` under the toolkit
root. Apply its adversarial plan/implementation/frozen-evidence checks inline when already
in a working session; do not spawn another supervisor solely because the agent is installed.

Record exact model/group/region, firmware, SDK/host and source SHA. The annual engine matrix
is vendor evidence, not product acceptance. A successful desktop/emulator test is not a
retail TV pass. Keep a pass/fail/skip register with measurable criteria and redacted evidence.
Before device work confirm the exact serial and operations, hold one Samsung-specific lane,
build first, and remove only owned forwards/processes in finally. Do not kill shared SDB,
reboot, install-permit or work around host privacy restrictions automatically.

Keep real tokens, server URLs, DUIDs, certificate files, account state and media out of
packages/logs/demo fixtures. Staging excludes common private/generated files; it is not a
secret scanner. Reject token-bearing redirects and unbounded XHR fallbacks; require full
response deadlines, byte limits and cancellation. ES5 parsing alone proves no runtime API.
Use synthetic content and explicit disposable test accounts, not household guest profiles.
Plex progress writes and Immich asset authorization are separate backend requirements.
For TV UX test focus, Back, held/repeated keys, decoded-image/Blob ownership, paginated
return-to-list state, resume/standby and The Frame Art Mode as distinct gates.

## Inspector boundaries

### Mandatory interactive acceptance workflow (unreleased candidate)

During authorized device UI work, open the standalone **tizen-live** canvas with an
absolute private `profilePath` before claiming live visual acceptance. Read
docs/live-canvas.md for installation, canonical ownership, grants and integration.
Use the canvas capabilities tool, open a caller-chosen instance, then invoke its
read-only `status` action to verify SDK routing. Only the human may Connect,
Start/Pause, Refresh or send input; the agent has no mutation action.
This is reusable for new apps: supply their explicit page/profile, never a Plex
identity assumed from an example. Preserve optional model/year/OS/firmware with
user-provided/observed/unknown provenance; do not infer hardware from an engine.
For input, use the visible remote buttons or focused arrow/Space/Enter/Backspace
shortcuts. Audit the app's non-write activation IDs before enabling OK/click;
leave the allowlist empty by default. Hold the canonical Samsung directory lane,
not flock or a separate lock, and confirm ownership-checked cleanup.
Require genuine native frames with source/time/provenance, and examine actual
results rather than infer them from successful RPCs. For periodic-feed acceptance,
observe at least three 10-second cycles on the named TV after its current lane owner
has released it. Do not perform real household playback/progress writes.
Use the app's explicit pairing flow and approved test accounts, not cookie,
session-storage or token extraction. Pixel grants may expose private on-screen
data; keep captures private and redact any evidence deliberately exported.
Host mock/UI fixture passes are not named-TV acceptance.

If the host has no extension/canvas capability, no authorized target, a held lane,
or an unsupported protocol, report the exact blocked/unavailable/unsupported state.
Continue host-only development; do not substitute desktop/emulated pixels or refuse
all building merely because live inspection is unavailable. Emulator comparison
requires an explicit separately labelled source. Never silently choose it for a TV.

On the observed 2017 Tizen 3.0 target, `shell 0 debug APP` failed with "closed", but an explicit
trailing `0` launched debugging (with a timeout-option warning). Use `--legacy-timeout 0` only
when relevant; never infer a general shell exists. Forward the returned inspector port to
loopback; inspect /json/version and /json/list. HTTP discovery alone is not a working screenshot.

For WebSocket protocol use bounded calls and no compression if required by the legacy server.
Page.canScreencast=false is an explicit unsupported result. A recognized captureScreenshot
request with no callback is a failure, not a PNG. Screenshots cannot prove AVPlay video-plane
composition or HDR/audio.
Do not generalize this to "no native image capture is possible": the parent investigation
reports LayerTree paint snapshots on the same engine. The unreleased standalone
candidate now ships an independently implemented LayerTree client, tested with host
fixtures, but has not rerun those upstream TV captures. Paint-layer replay is neither
panel/compositor capture nor a TV recording. Read the dated evidence entry before claiming
anything about completeness. Never use root/framebuffer or DRM bypass as a fallback.

## Handoff

State installed toolkit version, source SHA, invoked/read guidance, exact host commands and
results. Name unknown target features and ungranted operations. No synthetic successful
capture, signing verification or Store approval; a profile request alone proves none of them.
