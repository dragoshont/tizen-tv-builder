# Bounded OFF/ON benchmark

The checkout owns a reusable Python runner and offline report validator/summarizer:

```sh
python3 tools/tizen.py benchmark validate /PRIVATE/raw.json
python3 tools/tizen.py benchmark summarize /PRIVATE/raw.json
```

These commands need neither a TV nor an SDK. `validate` validates the raw schema,
sequence and provenance, not the truth of a measurement. A valid failed/cancelled
report exits 0 and retains its failure status. `summarize` derives statistics
without changing the input. Neither command certifies hardware or decides whether
an optional path is faster. This is current checkout functionality, not a new
published plugin version.

## Authorized live attachment

Prepare, audit, sign and deploy a **separate, reviewed account-free fixture** using
the existing [app flows](app-flows.md). Deployment needs its own exact authorization.
Verify the app/package/source and the mapping from the physical TV to an existing
loopback inspector forward and page ID. Do not use a production app or household
account. The caller owns that forward and the app lifecycle.

```sh
python3 tools/tizen.py benchmark run \
  --endpoint http://127.0.0.1:PORT --target PAGE_ID --device-alias tv-xxxxxx \
  --app PACKAGEID0.Probe --contract /PRIVATE/descriptor.json \
  --warmup 1 --samples 6 --timeout 120 \
  --authorized --reviewed-account-free \
  --metadata /PRIVATE/metadata.json --output /PRIVATE/raw.json
```

Replace all placeholders. `--device-alias` is the existing toolkit `tv-` plus
six-hex-digit alias for this TV, not its address or DUID. `--target` is the exact
inspector page ID, not the alias. The runner never selects the first page, scans,
connects SDB, installs, stops, relaunches or uninstalls an app. It never creates or
removes a forward. There is no production/account-write override.

`--authorized` grants this attachment/workload/cancellation on the named target.
`--reviewed-account-free` attests that the owner reviewed the **actual loaded
adapter and app**, including callbacks and cancellation, as account-free and
disposable. A name, descriptor flag, CSP, hash supplied by the app, or successful
`cancel()` acknowledgement is not independent proof of safety. The adapter runs
arbitrary app JavaScript; this is not a sandbox. Refuse the run if that review is
unavailable. No network/account data is needed by the included fixture.

The runner uses the existing `live_tv.Lease` directory/token mechanism. It takes
the canonical `/tmp/plx-samsung-device.lock` followed by
`/tmp/tizen-tv-tv-xxxxxx.lock` (OS temp directory on Windows), failing if either is
held. Do not nest it inside another holder of either lane. Release the setup
lease first while retaining ownership of your forward, and coordinate the entire
physical run with other tools/hosts. The current portal/Source connections are
stateless: their compatibility lease-path fields do not enforce mutual exclusion.
These host-local locks cannot verify physical-device ownership or stop
non-cooperating clients.

The deadline (1-120 seconds) covers connection, verification and all four blocks.
Inspector connection stages also have their existing four-second ceilings, clamped
to the overall deadline. Each evaluation is capped at five seconds or the remaining
budget. Cleanup has an additional two-second adapter RPC budget, then closes the
owned inspector socket and releases both owned leases in reverse order. The
caller must separately remove its forward and stop/uninstall its disposable app
under the existing authorized lifecycle. Cleanup failure is a failed run, not a
pass. A hung page may be unable to process cancellation; do not reuse it as though
cancellation succeeded.

The output must be a **new absolute path outside the toolkit**, in a private
directory. It is opened exclusively with mode 0600 (POSIX; use private directory
ACLs on Windows). Raw data is not printed to the terminal. On a collection failure
or Ctrl-C/SIGTERM, the output retains the valid sample prefix and failure/cleanup
status. Rejected malformed samples are not silently counted or repaired; their
raw invalid payload is not serialized. Output filesystem failure can prevent a
receipt; an empty/truncated file is not evidence of cleanup. JSON is capped at
16 MiB, with bounded per-sample arrays. Never commit private raw reports.

## Adapter protocol, version 1

Provide an ES5-compatible `window.__tvBenchmark` in the already loaded fixture.
No adapter code is injected by the toolkit. It first reads the current
`tizen.application.getCurrentApplication().appInfo` ID/version and user agent,
checks the app against `--app`, then requires exact equality between `describe()`
and `--contract`. These are observed page-reported identifiers, not cryptographic
verification of the installed package. A missing Tizen API is an explicit failure,
not a desktop fallback.

`describe()` returns exactly these fields (this example matches the included CPU
fixture and can be saved as its `descriptor.json`):

```json
{
  "schema": 1,
  "adapter_id": "toolkit-cpu-probe-v1",
  "app_id": "TvBench001.Probe",
  "source_id": "toolkit-cpu-probe-v1",
  "account_free": true,
  "metrics": {
    "operation_ms": {"unit": "ms", "meaning": "cpu-api-wall", "supported": true},
    "gpu_counter": {"unit": "count", "meaning": "counter", "supported": null}
  }
}
```

Identity strings are 1-256 printable ASCII characters. `source_id` should identify
the actual source/fixture revision, not just a product name. Declare 1-32 metrics
with names matching `[a-z][a-z0-9_]{0,63}`. Units: `ms`, `count`, `bytes`.
Meanings: `callback-interval`, `cpu-api-wall`, `elapsed`, `counter`; all timing
meanings require `ms`. `supported` is `true`, `false` or `null` (unknown). Unknown
counter names must be declared by the adapter; the toolkit has no universal GPU
counter registry and does not manufacture missing inspector counters.

For each block in **OFF / ON / ON / OFF** order, the runner issues the explicit
warmup count (0-20), then sample count (1-100). Counts are **per block**, not totals.
It calls `begin(request)` and requires `true`; begin must schedule bounded work
and return promptly. A request has exactly:

```json
{"id":"RUN_ID:0:sample:0","block":0,"mode":"off","phase":"sample","index":0}
```

`RUN_ID` is a fresh 32-hex-character run ID. Block is 0-3; phase is `warmup` or
`sample`; index starts at zero within each phase. Keep each ID with its actual
operation and reject overlapping/stale requests. Execute the same audited workload
and inputs in both modes; only the declared experimental switch should differ.

`poll(id)` returns one of:

```json
{"id":"RUN_ID:0:sample:0","state":"pending"}
```

```json
{"id":"RUN_ID:0:sample:0","state":"complete","metrics":{"operation_ms":[1.25,2.5]}}
```

An error state, thrown exception, missing/stale ID, unknown field, unexpected value
or unsupported method fails the run. Completed metrics can be omitted or `null`
when unavailable, **never zero as a substitute**. Only `supported:true` metrics
may return numeric arrays. Each nonempty array contains up to 4096 finite
nonnegative numbers at most `2 ** 53`; booleans, strings, NaN and Infinity fail.
One request can represent a whole scenario and return many original timings;
it does not mean one callback or one display frame.

`cancel()` must invalidate pending callbacks/actions, release resources it owns
and restore the fixture's in-memory experimental state; return `true` only after
synchronous teardown is complete. Callbacks already queued must check cancellation
before clicks, key events or new work. It must be safe after any partially completed
request. The runner calls it after success, failure and interruption, but only
after identity/descriptor verification; it does not call an unverified foreign
adapter's cleanup. No production preferences or account state may be changed.

## Provenance and statistics

Optional `--metadata` is an object whose allowed keys are `model`, `model_group`,
`region`, `firmware`, `runtime`, `source_sha`, `package_sha256`, `sdk_version`.
Each supplied entry has `value` (1-256 printable ASCII characters) and
`provenance` (`user-provided` or `observed`). Missing facts remain absent. Git SHA
is 40 lowercase hex digits; package SHA-256 is 64. Supply exact values with their
real provenance; app-reported `source_id` does not replace artifact verification.

```json
{"model":{"value":"EXACT_MODEL","provenance":"user-provided"},
 "runtime":{"value":"EXACT_ENGINE_VERSION","provenance":"observed"}}
```

Raw report schema 1 contains `run_id`, `plan`, `adapter`, `metadata`, `host`
(Python/platform/runner-file SHA-256), `target` (caller-supplied device alias/page
ID/provenance), `identity` (observed app ID/version/user agent), `records`, `status`,
`error`, and `cleanup`. Each record preserves its original `request` and completed
`result`. The plan retains order/counts/deadline. Cleanup separately names adapter,
connection, canonical `lease` and `target_lease`; status is `complete`, `failed` or
`cancelled`. The report intentionally does not contain the private endpoint,
serial, tokens, logs, DOM, page URLs or personal media.

The offline summary pools original arrays by metric and OFF/ON mode, excluding
requests whose phase is `warmup`. It reports count, measured/missing requests,
median, **nearest-rank P95** (sorted index `ceil(0.95*n)-1`) and `small_n` for
fewer than 20 observations. With no observations, statistics are null, not zero.
Missing requests or a failed run mark that metric's summary incomplete.
Warmup records are retained unchanged in the raw file. Pooling weights individual
observations, not requests equally; correlated timings are not independent trials.
For order effects, first activation and block-level analysis, inspect raw records.
There is no significance test or universal speedup threshold.

Animation callback intervals are **not panel FPS**. CPU/API wall time around
context/shader/upload/draw submission is **not GPU kernel execution time**.
Whole-operation elapsed time can include decode/poll/callback waits; label it
accordingly. OFF means the adapter's optional path is disabled, not that the TV's
GPU is disabled. These measurements cannot establish panel composition, AVPlay
performance, Store approval or support for other firmware/models.

## Independent fixture and external Plx integration

`tools/benchmark-fixture` is an independently authored MIT, account-free CPU
contract probe, not a graphics benchmark or copied app. It compares two arithmetic
implementations producing the same result. Its manifest has no internet privilege;
CSP denies connections. It has no account, media, network or storage code. Review
it like any other adapter; ES5 syntax alone does not prove runtime API support.

```sh
node --test tests/benchmark-fixture.test.mjs
python3 tools/tizen.py audit tools/benchmark-fixture
python3 tools/tizen.py pack tools/benchmark-fixture --output /PRIVATE/probe.wgt
```

The package is unsigned and not deployable as-is. Signing/installation/debugging
follow the existing explicit consent flow. Use `--app TvBench001.Probe` and the
descriptor above to collect after authorized setup.

The GPL Plx actual-0.2.7 experiment remains **external**. Its preparer and bridge
must stay in Plx, including notices. Prepare its separate account-free package,
verify its published-product hash and fixture hash, then give the toolkit its
adjacent descriptor JSON. The legacy workload's four whole-block invocations use
`--warmup 0 --samples 1`: the external bridge maps `request.block` 0-3 to its
existing block/navigation operations and exposes later-operation arrays plus
separately named internal-warmup arrays. The generic summary excludes only
request-phase warmups, so separately named warmup metrics remain separate statistics,
not automatically discarded. Do not claim that an internal warmup became a
measured steady-state operation. First activation and raw whole-block counters
also need separate declared metrics.

Do not load its GPL scripts into MIT-only toolkit modules, deploy production Plx,
or infer a general GPU benefit. The parent experiment reported the optional
WebGL artwork path slower for its measured workload, not a globally slow app or a
disabled GPU. This runner has host fake-inspector and fixture coverage only;
no TV benchmark acceptance is claimed here.
