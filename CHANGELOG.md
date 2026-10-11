# Changelog

Versions follow [Semantic Versioning](https://semver.org/). While we are below 1.0,
minor releases may change command contracts; patch releases fix defects without
intentionally breaking them. No version number is a claim of TV model support.

## Unreleased — live-canvas candidate

- Standalone scaffold-derived Copilot canvas with an independently implemented
  bounded localhost/CDP/LayerTree provider and user-only mutation routes.
- Native discrete 10-second feed, stale retention, pixel reuse, guarded controls,
  focused keyboard shortcuts, optional provenance-labelled metadata/comparison.
- Canonical Samsung atomic-directory lane; bounded connected runs are not TTL.
- Mandatory builder live-acceptance workflow with explicit unavailable status.
- Offline protocol/route/lifecycle/input/install gates; TV acceptance pending.
- No new published release or installed-plugin replacement.
- Runtime correction: permit host cross-site iframe loading of exact public
  renderer GET routes, while preserving authenticated API/POST origin checks.
- `python3 tools/tizen.py setup [--check]`: computer-only preparation (sdb and the
  tizen CLI); returns before any TV, certificate or app step.
- Guided setup with the same six steps and words in two places: the interactive
  `tv setup` and the portal's "Set up a new TV" wizard (opens automatically when no
  TV is ready). Developer Mode is polled, then connect, permission, app and screen.
- One-click certificate: `tv cert add TV` (and the portal button) adds a TV to the
  certificate after one Samsung sign-in, keeping the existing author certificate.
- Top-level TV commands: `scan`, `install PACKAGE_OR_HTTPS_URL TV` (bounded HTTPS
  download, validated `.wgt`, private temporary copy), `info TV [--app APP]`, `apps`,
  `open`, `screenshot`, `portal`. Legacy `install --serial` keeps its form.
- Install never uninstalls an app signed by another author; it says so and stops.
- Install fix: long TV progress no longer hides the final result. SDK output is
  drained into a bounded buffer that keeps the newest bytes under one deadline; the
  TV's app list then decides installed, not installed (absence proven) or unknown.
  An install refuses a `tizen -t` name shared by two connected TVs of one model.
- Batch certificate: `cert add TV1 TV2 ...` reads every TV's DUID first, then one
  Samsung sign-in covers all of them plus the TVs already covered, with the same author;
  already covered = no sign-in; non-zero exit if any TV is not allowed.
- `remove TV APP` (alias `uninstall`): reads the app list first (not installed = no-op,
  unreadable = stop), uninstalls exactly that app, confirms by the app list, prints a receipt.
- `cert reset PROFILE... [--yes]`: plan without `--yes`; with it, removes exactly those
  signing profiles (profiles.xml entry, certificate folder, macOS Keychain passwords)
  after a backup and receipt; refuses symlinks, unsafe names and shared folders.
- Not production-ready: live batch sign-in, remove/reset on a TV, live certificate sign-in, HTTPS-URL install, the Tizen Studio
  installer and Developer Mode detection on a fresh TV still need device acceptance.

## 0.1.0 - 2026-10-11

First versioned release.

- Agent Plugins 1.0 package: one portable skill, one Copilot agent, local Python tools.
- Release-pinned marketplace installation, rather than deprecated direct installs.
- Beginner instructions, annual Samsung engine matrix and an experience-coverage review.
- Manifest validation, staged unsigned builds, explicit signing/package and device helpers.
- Loopback inspector discovery with a total deadline and bounded responses.
- Owned POSIX SDK process cleanup on timeout, Ctrl-C and SIGTERM.
- Offline regressions and a real unsigned SDK build; no new retail-TV acceptance claim.

The initial unversioned bootstrap was `a66845d`. Deadline/process cleanup and the
source-verified annual engine matrix landed in `0932ab5`.
