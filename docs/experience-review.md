# Experience coverage: 0.1.0

Reviewed 2026-10-11 against the parent Samsung development study and this toolkit's
committed behavior. This is an inventory of what we preserved, not a promise that
we captured every discovery or implemented every helper.

| Experience | Preserved where | What the toolkit actually does | Remaining limit |
|---|---|---|---|
| Annual 2015-2026 engines, exact model/region/firmware | compatibility.md; builder skill | Documents vendor baseline and acceptance rows | No all-model test or native binary compatibility |
| Samsung Web .wgt, ten-character package/app IDs, TV privileges | lessons.md; manifest validator | Checks identity, TV profile, content and required_version | Not a complete Samsung schema/privilege validator |
| Samsung author/distributor profile, DUID, permit-install, error 118 | README; lessons.md | Explicit profile/serial commands; no automatic permit or certificate changes | Does not verify cryptographic signatures, Samsung profile type or target DUID |
| Safe source build before a short device lease | staged build; skill | Temporary allowlisted assets, unsigned host build | Staging is not secret scanning; callers still review sources |
| One Samsung-specific device lane, owned forwards and processes | AGENTS; skill; CLI tests | Explicit serial, no kill-server/all-forward cleanup; owned POSIX launcher-group cancellation | Caller supplies exclusive ownership; detached shared daemons are not killed |
| Debug "closed" is not a universal no-go; legacy trailing 0 | lessons.md; debug regression | Optional legacy argument, explicit target | Workaround observed on one TV, not required everywhere |
| Chrome 47 protocol, absent Schema, RPC timeout vs unsupported | lessons.md; skill | HTTP engine/protocol discovery; deadlines and byte limits | No WebSocket/RPC client bundled, no RPC regression claim |
| Screenshot/screencast failure versus LayerTree paint replay | dated lessons.md entry; upstream e48a829b | Documents upstream device observation, parent-inspected preview and missing layers | No capture helper, toolkit rerun or panel completeness proof here |
| No retail general shell/root; root-only framebuffer recipe | lessons.md; policy | No generic shell/root/framebuffer/reboot command | Not a workaround to implement |
| macOS Local Network permission, SDB ownership | lessons.md | Guidance; no automatic relay/bypass/server reset | Owner resolves host permissions |
| Apple Silicon emulator architecture vs GPU toggle; Windows | lessons.md; host limits | Honest architecture/SDK caveats | No verified ARM emulator launch; Windows SDK wrappers deliberately unsupported |
| AVPlay versus MSE, 2018 FHD/DTS statements, 2017 clip tuples | compatibility.md; lessons.md | Source-backed boundaries; per-model acceptance procedure | No player, codec promise, HDR/audio/panel capture proof |
| Token redirects, unbounded XHR, response deadlines/cancellation | lessons.md; skill | Bounded loopback discovery, redacted metadata | App networking needs its own safe implementation/tests |
| Focus/Back/held keys, decoded images/Blob lifetime, pagination return | lessons.md; skill | Operational checklist | No product UI or device UX acceptance bundled |
| The Frame Art Mode, standby and return-to-app | compatibility.md; skill | Separate acceptance gates | No Art Mode helper or native state-control claim |
| Household guest users, auth/storage privacy, Plex versus Immich | lessons.md; skill | Synthetic content and explicit disposable accounts; backend-specific contracts | No server/account writes or Immich client implemented |
| Publisher/version identity and practical reuse | plugin.json; release.json; CHANGELOG | Agent Plugins 1.0 skill and Copilot agent; deterministic ZIP, source SHA/file hashes | Private repo needs access; checksums are not signed attestation |

## Adversarial checks before calling this complete

1. Does a passed build prove TV support? **No.** Mark host, emulator, owner-reported
   and device-observed evidence separately. Name source SHA, model and firmware.
2. Does `--authorized` lock the TV or validate consent? **No.** The caller owns the
   exact operation grant and exclusive Samsung lane. Clean up only owned resources.
3. Does staging make a package safe? **No.** Allowed HTML/JS/images can contain
   credentials or personal data. Review sources and test fixtures before release.
4. Does an SDK profile request prove a valid retail package? **No.** Cryptographic
   signing, Samsung profile type, DUID and actual install are separate gates.
5. Do failed screenshot RPCs prove no native image path exists? **No.** Preserve
   the new paint-replay report without relabeling it as a panel capture or recording.
6. Is install registration the same as loaded guidance? **No.** Verify plugin
   version and skill discovery, then actually load/read the installed skill and
   exercise it in the target session.
7. Do the tests cover WebSocket/RPC behavior? **No.** They cover command contracts,
   HTTP limits/privacy and owned process lifetimes. The GPL inspector stays upstream.
8. Is every lesson equally strong? **No.** Official tables, named-model study
   observations, candidate reports and host fixtures are different evidence kinds.

## Unreleased canvas continuation

The 0.1.0 inventory above remains historical. The current candidate adds a
stdlib uncompressed WebSocket/RPC/LayerTree implementation, canonical directory
lease, authenticated localhost routes, user-only feed/input, metadata persistence
and a scaffold-derived standalone SDK canvas. The skill/agent now requires opening
it for authorized live UI acceptance, or recording an actual blocker. See
[the integration and evidence contract](live-canvas.md).

Host protocol/route/input/lifecycle/install tests and a timed three-cycle mock
feed do not establish native TV performance or input fidelity. SDK registration,
offline opening/status/schema routing and real browser UI testing are separate
host gates. Parent-reported 406 ms native capture belongs to the upstream viewer,
not this candidate. Existing Samsung lane ownership is never overridden.

## Source boundaries

Stable references: [Samsung engine table](https://developer.samsung.com/smarttv/develop/specifications/web-engine-specifications.html),
[Samsung CLI](https://developer.samsung.com/smarttv/develop/getting-started/using-sdk/command-line-interface.html),
[Samsung Web Inspector](https://developer.samsung.com/smarttv/develop/getting-started/using-sdk/web-inspector.html),
and [committed PlxNative inspector study](https://github.com/dragoshont/plx-native/blob/e48a829b/docs/samsung-web-inspector.md).
The study branch is a moving reference; do not call an unmerged candidate a
permanent upstream fact. No private receipt, personal artwork, certificate or
copied GPL implementation is included.
