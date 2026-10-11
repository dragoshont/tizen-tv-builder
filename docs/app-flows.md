# App flows: new app, checks, retail TV, emulator, budgets

One CLI, the same on macOS and Windows (`python3` or `py -3`). Every command prints
JSON and exits `0` on pass, `2` when a check fails, `1` on an error.

## Start and check an app (no SDK, no TV)

```sh
python3 tools/tizen.py new ./MyTv --name "My TV"      # unique 10-char package ID, ES5 template, host tests
node --test MyTv/tests/focus.test.js                   # template host tests
python3 tools/tizen.py audit MyTv/app                   # IDs, privileges, ES5, credential leaks
python3 tools/tizen.py pack MyTv/app --output my.wgt    # deterministic unsigned package
python3 tools/tizen.py check-package my.wgt             # manifest + signature consistency
```

`audit` errors (missing privilege for a used API, ES2015 syntax, a credential-like
value, ID mismatch) also block `build` and `package`. Warnings (unused privilege,
wildcard `<access>`, example ID) do not. It reports file:line only, never the value.

After signing with Tizen Studio, prove the signature covers every file:

```sh
python3 tools/tizen.py check-package MyTv.wgt --require-signed
```

## Retail TV: install, measure, capture, clean up

Needs the free device lane, the TV serial and explicit authorization:

```sh
python3 tools/tizen.py identify --serial TV_IP:26101 --authorized   # prints "fingerprint": "tv-…"
python3 tools/tizen.py retail --serial TV_IP:26101 --authorized \
  --target tv-0123456789abcdef --app ABCDEFGHIJ.MyTv --package MyTv.wgt \
  --keys down,up --runs 3 --tier current --output ./evidence --uninstall
```

- Takes the lane (`/tmp/plx-samsung-device.lock`, tempdir on Windows) and refuses if held.
- `--target` is the fingerprint from `identify` (a hash of the DUID); a different TV is refused before install.
- Requires a signed package. Kill-before-debug, owned forward only, removed afterwards.
- Prints a JSON receipt: cold start, first frame, first Home per run and the budget
  verdict. `--output` gets real PNG captures only (unsupported capture is recorded in
  the receipt) and a redacted `dlog-tail.txt`.
- `identify` never prints the DUID; use Device Manager / `sdb shell 0 getduid` privately.

## Emulator

```sh
python3 tools/tizen.py emulator --vm T-samsung-10.0-x86_64 --app ABCDEFGHIJ.MyTv \
  --package MyTv.wgt --keys down,up --output ./evidence
```

Starts the VM it owns, installs, launches, measures, captures, and stops only that VM.
The Samsung TV emulator is x86_64 and needs hardware virtualization (HAXM, WHPX or KVM):
it does **not** run on Apple Silicon. There the flow fails within seconds with the
launcher's reason.

## Budgets

`docs/budgets.json` holds per-tier targets (cold start, first frame, first Home).
They are targets, not measurements. Check measured numbers:

```sh
python3 tools/tizen.py budgets --tier legacy --metrics metrics.json
```

`metrics.json` uses `coldStartMs`, `firstFrameMs`, `firstHomeMs`. Missing metrics are
reported as `unmeasured`, never as pass. Apps report marks via `window.__tvMarks`
(the template does this).

## Proven so far

- Generated app: scaffold → host tests → audit → pack → check, in CI on Ubuntu and Windows.
- Real Tizen Studio build and sign of a generated app on macOS; `check-package --require-signed` passed.
- Retail and emulator flows: tested against fakes. A real retail run on a new TV, a real
  emulator run (needs an x86 host) and measured budgets are not yet observed.
