# Record one native app layer

This is an unreleased host implementation, not TV acceptance. A recording is a
bounded sequence of genuine `LayerTree.makeSnapshot` / `replaySnapshot` PNGs from
one selected **app-layer**. It is not a panel/compositor capture, a composite,
AVPlay/video-plane recording, HDR or audio. No reconstructed or interpolated
frames are generated. The [2017 observation](lessons.md#capture-update-keep-the-artifact-types-separate)
establishes paint replay separately from Page screenshot/screencast support.

## Formats and limits

PNG sequence plus a manifest always works without an encoder; a PNG still uses
the first captured frame. The original native PNGs are retained at their original
dimensions. `size` is the maximum bounding box for **encoded** GIF/video/WebP;
resizing preserves aspect ratio, rounds dimensions to even values, and uses
Lanczos filtering. It does not establish that a selected layer is visually complete.

An already-installed `ffmpeg` on PATH optionally provides:

| Export | Required installed capabilities | Implementation |
|---|---|---|
| GIF | GIF encoder, palettegen and paletteuse filters | cross-frame palette, Bayer dithering, rectangle-difference optimization |
| MP4 | libx264 | CRF 28, veryfast, yuv420p; fragmented MP4 for bounded pipe output |
| WebM | libvpx-vp9 | CRF 36, realtime/cpu-used 6; no audio |
| WebP | libwebp | one still, quality 80; animated WebP/sequence encoding is not implemented |

There is no automatic installation or bundled encoder, GPL library, SDK or
external GIF tool. If ffmpeg is absent, show its setup explanation and offer PNG.
Installation needs the computer owner's permission; ffmpeg build licensing and
codec availability vary. An available encoder can still fail on a particular
input/build: preserve PNG and report failure, not success.

Bounds: positive FPS through 15; encoded size 2..1280 by 2..720; positive duration
through 120 seconds; at most 120 frames and 32 MiB **including** manifest and
admitted exports. Capture waits after each completed request, so slow capture
lowers actual sampling FPS. No catch-up burst, overlap, automatic retry or queue.
The manifest records actual completion timestamps; animated exports play admitted
frames at nominal FPS, so they can be shorter than elapsed capture time. They are
not real-time screen/video recordings. Identical captured paints are valid genuine
samples, not synthesized motion.

The existing source lease is never extended. Scheduling ends at the earlier of
the requested duration and source expiry; an in-flight source capture must finish
its existing protocol deadline and snapshot cleanup. Stop joins for at most 35
seconds. A non-cooperative source that misses this deadline is explicitly failed,
retains its recording ownership, and cannot export while its thread lives. It
cannot safely be force-killed in Python. The backend expects the existing bounded
`Source`, not arbitrary unbounded capture callbacks.

## Local-browser integration

Import from `tools/recording.py`; there is deliberately no device-command CLI or
agent-callable capture interface.

```python
from recording import Encoder, Recorder, RecordingError

encoder = Encoder()                 # local executable capability probe, no TV calls
capabilities = encoder.summary()    # safe for a public capabilities response

recorder = Recorder(
    source,
    "/PRIVATE_EXISTING_PARENT/app-layer-0-UNIQUE_NAME",
    fps=2, size=(960, 540), duration=30, layer_index=0, encoder=encoder,
)
public_status = recorder.start()
public_status = recorder.status()
private_receipt = recorder.stop()   # signal, bounded join, manifest receipt
private_receipt = recorder.export("gif")
private_receipt = recorder.export("png", still=True)
recorder.close()                   # stop/join and release owned directory handle
```

The output path means the **new recording directory**, not a filename or existing
parent. Require an absolute path outside this toolkit checkout, an existing
non-symlink parent, and a basename `app-layer-N-SUFFIX` matching the selected layer.
The directory is exclusively created with mode 0700; files use exclusive creation
and mode 0600 on POSIX. Existing names are never overwritten. POSIX directory
traversal still uses directory descriptors and `O_NOFOLLOW`.

Windows uses only built-in system tools and Win32 APIs, without installations:

- `System32/whoami.exe /user /fo csv /nh` observes the current user's SID.
  System tools are not discovered from the checkout or user-controlled PATH.
- Each existing ancestor is opened with `CreateFileW`, reparse-point opening and
  backup-semantics flags. Read-sharing-only pinned handles prevent ancestor
  rename/delete replacement while the recorder lives. Reparse points, UNC/device
  paths, non-fixed drives and unavailable handles are refused.
- `CreateDirectoryW` creates the new folder exclusively **with** a protected,
  current-user-only owner/DACL security descriptor. There is no interval where
  native pixels are written into a publicly inherited directory.
- `icacls.exe /inheritance:r /grant:r *CURRENT_SID:(OI)(CI)F /q` reinforces the
  private inheritable full-control grant. Both commands have five-second deadlines
  and 16-KiB output limits. Tool output/SIDs/private paths are never logged.
- `GetSecurityInfo` verifies the owner, protected DACL and exactly one full-control
  current-user ACE, before and after icacls. New files use exclusive native
  `CreateFileW` with the current-SID owner and protected private DACL at creation;
  their owner/DACL and handle/path identity
  are verified before writing any bytes. Reads also reject reparse files,
  hardlinks and identity mismatch. Encoding receives hash-verified native bytes
  over stdin, never paths the encoder might follow.
- ACL/API/command failures are explicit; there is no permissive chmod-only or
  generic Windows fallback. Elevated tokens receive the explicit current-SID
  file owner rather than their potentially different default owner; mismatches
  are still refused, never silently granted broader access.
  An ACL setup failure can leave an empty private output folder; it contains no
  captured pixels. Administrators' ability to take ownership is an OS boundary,
  not a guarantee this recorder can remove.

Native Windows ACL/file/PNG export and loopback lifecycle passed on hosted Windows
Server 2025 at source `48862db`; see the [dated CI evidence](workbench.md).
Desktop Windows 10/11 and Windows-to-TV acceptance remain pending. Mock tests
separately verify failure decisions. Restrictive ancestor handle sharing can refuse folders already
held for incompatible writes; choose a private existing parent rather than
weakening the checks. Outputs remain after close for the local user; no bulk
cleanup or deletion of private recordings is performed.

Use authenticated local-browser POST routes for start, stop and export, with the
same loopback/Host/Origin/CSRF policy as other private source operations. Validate
the exact request keys and types. Do not expose an agent route or treat merely
opening the UI as target/pixel authorization. The source must already be connected
under its own target authorization with **separate pixel consent**. An exported
receipt contains paths and must only reach the authenticated local browser, never
logs, public status or a repository artifact.

Keep one recorder per source. Pause existing native preview streaming before
start. While recording, the controller should reject competing capture/stream
start routes rather than queue them; the recorder also refuses an observed source
lock conflict or stale revision. Remote actions may remain explicitly authorized,
but racing one with capture can honestly stop recording. Source disconnect and
server teardown must call `stop()` / `close()` before discarding the source. Handle
`RecordingError.code` and show its bounded message; sanitize unexpected exceptions
at the authenticated route boundary. Encoder export is synchronous and bounded
to 40 seconds plus owned-process cleanup; capability probes are five seconds each.

`status()` returns `fps`, `size`, `duration`, elapsed `length`, `frames`, `state`,
`error`, `layer_index`, and encoder availability. It never returns paths, hashes,
pixel bytes or inspector addresses. States are `idle`, `recording`, `stopped`,
`failed`; recorders are one-shot. `stop()`, `receipt()` and `export()` return that
status plus private `output_path`, `manifest` and `paths`. A failed run can retain
partial genuine PNG frames and its failure-state manifest; never label it complete.

Selecting layer index N is positional in the source's native paint list, **not** a
stable DOM/compositor identity. Missing layers or changing dimensions stop capture.
Same-size layer reorder cannot be detected with the current Source frame contract;
do not promise identity continuity or silently composite other layers. Keep the
filename and preview tooltip labeled “app-layer N”; use capabilities-first short
UI wording, useful FPS/size/elapsed/frame counts and a clear private output folder,
not a promotional recording banner.

## Why optional ffmpeg

Research checked 2026-10-11 before implementation:

- [gifski upstream](https://github.com/ImageOptim/gifski) describes cross-frame
  palettes and temporal dithering, and says size reduction has the largest effect.
  Its license is AGPL-3.0-or-later with alternative commercial licensing; it is not
  bundled. GitHub API reported **5,666 stars**.
- [gifsicle upstream](https://github.com/kohler/gifsicle) is a GIF manipulation/
  optimization tool, not a general video exporter. GitHub API reported **4,328
  stars**, GPL-2.0 detection; it is not bundled.
- [FFmpeg upstream](https://github.com/FFmpeg/FFmpeg) reported **64,974 stars**.
  [Official palettegen/paletteuse documentation](https://ffmpeg.org/ffmpeg-filters.html#palettegen)
  documents palette generation, dithering and difference rectangle controls.
  The implementation uses these through an optional executable, with bounded
  resolution/FPS and pipe output; no source or binaries are redistributed.
- [HN: Gifski: Optimized GIF Encoder](https://news.ycombinator.com/item?id=40426442)
  is a real discussion; the
  [HN item API](https://hacker-news.firebaseio.com/v0/item/40426442.json) reported
  256 points and 64 descendants. Popularity/discussion is not a compression
  benchmark or device acceptance.
- Reddit discussion was explicitly searched, but searches did not yield a
  verifiable direct discussion and Reddit's search JSON returned HTTP 403. No
  “Reddit consensus,” quote, recommendation or engagement count is claimed.
- [CDP Page.startScreencast](https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-startScreencast)
  describes a protocol method, not annual Samsung TV support. The repository's
  actual legacy observation was `Page.canScreencast=false` and start with no frames.
  This recorder therefore uses discrete native paint captures, never Page
  screencast or an invented screenshot fallback.

Stars are a dated popularity snapshot, not quality evidence. GIF palette/dither
settings reduce common naive-encoding problems but do not guarantee a smallest
file or perceptual optimum. MP4/WebM size and decoder/browser playback acceptance
remain input-, build- and platform-dependent.

## Host tests

`python3 -m unittest discover -s tests -p test_recording.py -v` exercises mocked
native sources/encoders, private output policy (including mocked Windows SID/ACL/reparse/identity failures),
limits, overlap, stop/join, failure
cleanup, tampered-frame refusal and encoder process bounds. Tests use named
checkout-local scratch folders with only the repository-root boundary mocked;
they do not connect to devices, capture accounts or install tools. Host tests are
not hardware or visual-quality evidence.

The dated [bounded TV observation](workbench.md#bounded-retail-observation-2026-10-11)
additionally exercised nine genuine native paint frames and PNG/GIF/MP4/WebM
exports. WebM explicitly uses `yuv420p`: automatic negotiation from renderer
PNG colour/alpha formats can select unsupported VP9 input. Encoded video
formats do not preserve the PNG alpha channel and still omit AVPlay video.
