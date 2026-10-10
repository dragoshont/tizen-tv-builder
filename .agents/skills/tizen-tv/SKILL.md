---
name: tizen-tv
description: Source-backed Samsung Tizen retail-TV Web app build, signing, installation and Web Inspector troubleshooting.
---

Read README.md and docs/lessons.md at this skill's repository root.
Use `python3 tools/tizen.py --help`; run doctor before assuming SDK availability.

Workflow: inspect named model and official engine/API tables -> validate manifest -> host tests
and unsigned source build -> certificate profile -> explicit authorized install -> named-model
remote/media/lifecycle acceptance -> clean teardown and evidence receipt.

Do not install SDKs automatically. The toolkit wraps an already-installed Tizen CLI/SDB.
Unsigned build is not a signed developer package. A Samsung distributor certificate must include
the target DUID; certificate values remain private. Package ID is ten alphanumeric characters.

On the observed 2017 Tizen 3.0 target, `shell 0 debug APP` failed with "closed", but an explicit
trailing `0` launched debugging (with a timeout-option warning). Use `--legacy-timeout 0` only
when relevant; never infer a general shell exists. Forward the returned inspector port to
loopback; inspect /json/version and /json/list. HTTP discovery alone is not a working screenshot.

For WebSocket protocol use bounded calls and no compression if required by the legacy server.
Page.canScreencast=false is an explicit unsupported result. A recognized captureScreenshot
request with no callback is a failure, not a PNG. Screenshots cannot prove AVPlay video-plane
composition or HDR/audio.
