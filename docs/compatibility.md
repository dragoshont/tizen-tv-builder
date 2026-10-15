# Compatibility is an evidence matrix

Checked against Samsung's documentation and the PlxNative investigation on 2026-10-11.
The target scope begins in 2015; untested years are **unknown**, not supported.

## Annual engine baseline

These are vendor-documented engine generations, not application support claims.
Source: [Samsung Web engine specifications](https://developer.samsung.com/smarttv/develop/specifications/web-engine-specifications.html),
retrieved 2026-10-11. Recheck the live table before choosing a target or SDK.

| Model year | Tizen | Engine baseline | This toolkit's retail acceptance |
|---|---|---|---|
| 2015 | 2.3 | WebKit r152340 | Not tested |
| 2016 | 2.4 | WebKit r152340 | Not tested |
| 2017 | 3.0 | Chromium M47 | Not tested; separate study observed one set |
| 2018 | 4.0 | Chromium M56 | Not tested |
| 2019 | 5.0 | Chromium M63 | Not tested |
| 2020 | 5.5 | Chromium M69 | Not tested |
| 2021 | 6.0 | Chromium M76 | Not tested |
| 2022 | 6.5 | Chromium M85 | Not tested |
| 2023 | 7.0 | Chromium M94 | Not tested |
| 2024 | 8.0 | Chromium M108 | Not tested |
| 2025 | 9.0 | Chromium M120 | Not tested |
| 2026 | 10.0 | Chromium M130 | Not tested |

### Later main-checkout observations (12 October 2026)

The annual table above is the original dated acceptance inventory, not the latest
list of owner observations. The main checkout subsequently exercised Plx development
operations on MU6172 (2017), RU7092 (2019), Q80C (2023) and QN70F (2025) TVs:
discovery/connect, app-list reads, certificate coverage, signing and verified installs.
Live certificate issuance and renderer/input operations have narrower receipts; a
successful install does not prove playback, physical-remote parity or every capture
path. Layer-limit and newer-inspector capture failures remain explicit limitations.
No broader firmware matrix or Store certification is inferred.

### Updated-runtime observation (15 October 2026)

The parent reports Vemm 0.0.1 from source `2fc024c8` installed and launched on
QE65Q80CATXXH (2023) running **observed Tizen 9.0**. Do not replace that observation
with the annual 7.0 baseline or infer an engine version from it. The inspector
rejected `LayerTree.makeSnapshot`; no device screenshot or runtime CSS receipt
was obtained during deployment. A later parent-owned smoke verified app identity,
debug launch and 1920x1080 body geometry without horizontal overflow, not a screenshot.
This narrows install/launch evidence, not playback, capture, firmware
or all-model acceptance. [Deployment details](lessons.md#vemm-deployment-observation-15-october-2026).
The final parent-reported install/runtime smoke binds current Vemm evidence to
`2b50628cd9a346dbe1d374e103124665b749b260`: appInfo 0.0.1, profile-picker state,
1920x1080/no overflow and matching rebuilt payload hashes, still without capture,
remote, media/account or physical-panel acceptance.

For every row, separately verify Samsung TV author/distributor signing, target DUID and
installation permission, privileges/required_version, runtime APIs and product behavior.
Engine generation alone proves none of those. Samsung's HTML uses shared, two-row cells
for the 2015/2016 WebKit type and revision; flattened text can hide the 2015 values.
No future-year or native-binary compatibility is implied.

## Capability boundaries

| Cohort | Established boundary | Acceptance still required |
|---|---|---|
| 2015-2016 | Samsung documents Web Inspector since 2015; legacy engines and API versions vary | Exact model/firmware, packaging, JavaScript runtime APIs, TLS, graphics, AVPlay, remote and lifecycle |
| 2017 observed UE55MU6172 | Tizen 3.0, inspector Chrome 47 / protocol 1.1; signed app installation and debug connection observed in PlxNative study | This toolkit's deployment; product-specific acceptance and broad media tuples |
| 2018 | Tizen 4.0 / Chromium 56; Samsung documents MSE UHD restriction and unsupported DTS | AVPlay UHD/HDR and model-group codec/audio/subtitle behavior |
| 2019-current | Engines/APIs change across annual model groups; use current vendor tables | Every claimed target row, older/newer engine compatibility, signing/update/Store acceptance |
| The Frame, any year | Product line, not a single runtime | Art Mode, standby and return-to-app in addition to ordinary model acceptance |

Author-reported or probe-clip playback does not establish a finished application's behavior.
Host ES5 tests do not prove fetch, streaming response readers, TextDecoder, WebAssembly or
decoder support. Conservative negotiation must retain supported fallbacks and honest errors.

## Acceptance template

Record privately: case ID, product/source SHA, package checksum, model/group/region, firmware,
SDK/host version, input/media tuple, expected outcome, actual result, evidence kind and artifact.
Publish a redacted register with pass/fail/skip and known limitations.

Minimum gates: signed install/launch/update/uninstall; normal TLS and bounded network handling;
remote focus/Back/held/repeated input; original/transcoded media tuples; track/subtitle changes;
memory/resource ceilings; background/standby/no-unintended-playback; account deletion and data
privacy. Test backend contracts separately (Plex progress is not an Immich requirement).

Store signing/review and branding are separate from developer installation.

## Primary sources

- [Samsung Web engine specifications](https://developer.samsung.com/smarttv/develop/specifications/web-engine-specifications.html)
- [Samsung media specifications](https://developer.samsung.com/smarttv/develop/specifications/media-specifications.html)
- [Samsung 2018 video specifications: MSE FHD limit and DTS exclusion](https://developer.samsung.com/smarttv/develop/specifications/media-specifications/2018-tv-video-specifications.html) (retrieved 2026-10-11)
- [Samsung MSE](https://developer.samsung.com/smarttv/develop/guides/multimedia/media-streaming.html)
- [Samsung AVPlay](https://developer.samsung.com/smarttv/develop/api-references/samsung-product-api-references/avplay-api.html)
- [Samsung Web Inspector](https://developer.samsung.com/smarttv/develop/getting-started/using-sdk/web-inspector.html)
- [Samsung certificates](https://developer.samsung.com/smarttv/develop/getting-started/setting-up-sdk/creating-certificates.html)
- [Samsung TV connection](https://developer.samsung.com/smarttv/develop/getting-started/using-sdk/tv-device.html)

Recheck the live vendor tables before expanding support. The annual rows above are a dated
vendor snapshot, not guesses for future years.
