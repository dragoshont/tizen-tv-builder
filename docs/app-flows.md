# App flows: new app, checks, retail TV, emulator, budgets

The low-level app/check commands on this page print JSON and exit `0` on pass or
`2` on a failed check, step, teardown, budget or SDK/connection error. The human-facing
`tv` commands use readable status/error text instead; see [getting started](getting-started.md).
Use `python3` on macOS/Linux or `py -3` on Windows. Low-level SDK wrappers remain
POSIX-only; the Workbench has separate Windows support. A Python traceback is a
toolkit bug, not a successful operation.

## Start and check an app (no SDK, no TV)

```sh
python3 tools/tizen.py new ./MyTv --name "My TV"      # unique 10-char package ID, ES5 template, host tests
node --test MyTv/tests/focus.test.js                   # template host tests
python3 tools/tizen.py audit MyTv/app                   # IDs, privileges, ES5, credential leaks
python3 tools/tizen.py pack MyTv/app --output my.wgt    # deterministic unsigned package
python3 tools/tizen.py check-package my.wgt             # manifest, audit and safe-archive check
```

`audit` errors (missing privilege for a used API, ES2015 syntax, a credential-like
value, ID mismatch) also block `build` and `package`. Warnings (unused privilege,
wildcard `<access>`, example ID) do not. It reports file:line only, never the value.

If `config.xml` deliberately omits the application ID because staging injects it,
`audit` warns `app-id-unset`; pass `--id PACKAGE.Application` to check it (a value
that disagrees with a declared ID is an error). `validate`, `build` and `package`
still require the ID. A network API passed by reference (`var f = fetch`) counts
as using `internet`; `typeof fetch` feature detection does not. A private address
inside a prose string (`"…such as http://192.168.1.10:32400"`) is treated as an
example; one assigned as a value is still a warning.

After signing with Tizen Studio, check the signature files are complete and their
digests cover every file:

```sh
python3 tools/tizen.py check-package MyTv.wgt --require-signed
```

This is a structural and digest check, not signature verification. It requires an
author and a distributor signature, each with SignedInfo, a known SignatureMethod,
a SignatureValue, an X.509 certificate, the correct role and resolvable property
references, and the distributor must also cover `author-signature.xml`. It does not
verify the RSA/ECDSA value, the certificate chain or the DUID; the TV does that at
install. Archives with drive letters, colons, rooted, `.`/`..`, empty or symlink
entries are refused before extraction. Text and script files over 4 MiB fail the
audit instead of being packaged unscanned.

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
- Requires a package that passes `--require-signed` and whose app ID equals `--app`;
  a mismatch is refused before the lane is taken or anything is installed. Kill-before-debug, owned forward only, removed afterwards.
- Prints a JSON receipt: cold start, first frame, first Home per run and the budget
  verdict. `--output` gets real PNG captures only (unsupported capture is recorded in
  the receipt) and a redacted `dlog-tail.txt`.
- `passed` is false (exit `2`) if any required step, capture or teardown failed or the
  budget did not pass; `failed_steps` names them. `sdb shell 0 kill <package>` exits
  `0` even when it fails, so the exit status proves nothing. The output must be exactly
  one of the wordings observed on a Tizen 3.0 retail TV, optionally followed by one
  `spend time for pkgcmd is [N]ms` line: `Pkgid: <package> is Terminated` (`ok`) or
  `Pkgid: <package> is already Terminated` (`not-running`).
  `processing result : General error [-1] failed` is `failed` (`sdk_kill_error`).
  Any other, mixed or extra output is `failed` (`sdk_kill_unrecognized`). SDK output
  longer than the runner limit (512 KiB by default) is rejected as
  `sdk_output_truncated` whatever the exit code, so a kill fails and a `dlog` tail is
  recorded as `unsupported`.
  A nonzero exit, a timeout or a lost connection is always `failed`. Other firmware
  may use different wording; that fails closed until it has been observed.
  Any missing or non-numeric run sample makes that median
  `invalid`. Budget limits and measurements must be
  finite, non-negative numbers no greater than `2 ** 53` milliseconds; anything else
  is `invalid`, including oversized integers, without floating-point conversion.
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

For collecting reusable OFF/ON measurements rather than evaluating existing startup
marks, use the separate [ABBA benchmark runner](benchmark.md). It attaches only to
an explicitly reviewed account-free app/adapter and does not run this page's
install/debug/teardown flow automatically.

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
