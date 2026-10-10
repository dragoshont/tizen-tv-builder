# Lessons from a real development week

These are independently expressed findings from the October 2026
[PlxNative Samsung investigation](https://github.com/dragoshont/plx-native/tree/dragoshont-samsung-tizen-feasibility).
They are not transplanted client code. Named hardware observations belong to that project,
not a completed device run of this new toolkit.

## Deployment and host

- A public retail-TV Web app uses `.wgt` and Samsung signing; Linux underneath does not confer
  root, a general shell, native daemon deployment or LG ABI compatibility.
- The observed package identifier was ten alphanumeric characters. Keep app ID/package ID
  consistent in the manifest and run command; the display name determines package filenames.
- Missing install permission produced error 118; do not diagnose every failure as bad signing.
  Distributor certificates bind target DUIDs. Do not publish certificates or DUIDs.
- Certificate Manager and SDB server ownership can interfere. Never kill shared servers blindly.
- macOS Local Network permission may block the app launching SDB while Apple's `nc` connects.
  Fix the launching app's permission first. A previously approved relay is an environment-specific
  workaround, not a default security bypass to distribute.
- Installed x86 emulator images did not establish execution on Apple Silicon. GPU-off does not
  solve absent x86 CPU virtualization. Check shipped vendor architecture support, not announcements.
- One physical TV means one exclusive device worker. Build and inspect artifacts outside the
  device lease. Only remove forwards/processes you own.

## Inspector: important correction

The 2017 target initially answered `sdb shell 0 debug APP` with `closed`. This did **not** prove
that debugging was unavailable. Adding explicit trailing `0` launched a debug port despite
an unrecognized timeout-option warning. A similar older-device workaround was reported in
[Samsung/Wits#11](https://github.com/Samsung/Wits/issues/11).

Observed target: UE55MU6172, Tizen 3.0, Chrome 47.0.2526.69 protocol 1.1. HTTP target discovery
worked. An uncompressed WebSocket client worked where the newer built-in client's handshake
did not. Runtime evaluation and Page.enable answered; Schema.getDomains was absent.
Page.captureScreenshot was recognized but never returned an image within bounded attempts.
Page.canScreencast returned false, start accepted without frames and stop answered.

Therefore: inspect live CSS/geometry/errors, but do not advertise native screenshots/recording
on this target. Samsung documents debugging on TVs since 2015, not every DevTools protocol
method. Debugging can change application lifecycle; no automatic reboot as cleanup.

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
