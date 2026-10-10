# Compatibility is an evidence matrix

Checked against Samsung's documentation and the PlxNative investigation on 2026-10-11.
The target scope begins in 2015; untested years are **unknown**, not supported.

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
- [Samsung MSE](https://developer.samsung.com/smarttv/develop/guides/multimedia/media-streaming.html)
- [Samsung AVPlay](https://developer.samsung.com/smarttv/develop/api-references/samsung-product-api-references/avplay-api.html)
- [Samsung Web Inspector](https://developer.samsung.com/smarttv/develop/getting-started/using-sdk/web-inspector.html)
- [Samsung certificates](https://developer.samsung.com/smarttv/develop/getting-started/setting-up-sdk/creating-certificates.html)
- [Samsung TV connection](https://developer.samsung.com/smarttv/develop/getting-started/using-sdk/tv-device.html)

Recheck the live vendor tables before expanding support. This document deliberately does not
freeze a guessed Chromium version for every future year.
