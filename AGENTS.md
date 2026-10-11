# Tizen TV builder working policy

Read README.md, docs/compatibility.md and docs/lessons.md first. This toolkit builds Samsung
TV Web applications; it does not certify hardware support from successful host tests.
Canonical agent: com.github.copilot/agents/tizen-tv-builder.agent.md. Canonical skill:
skills/tizen-tv-builder/SKILL.md. plugin.json owns the release version.

For a first app, read docs/prerequisites.md and docs/getting-started.md.
Generic workflow skills are auto-discovered from skills/*/SKILL.md; this checkout adds
samsung-tv-lease, samsung-tv-connect, samsung-tv-debug, samsung-tv-inspect,
samsung-tv-certificates and samsung-tv-new-app. Their shared contract is skills/common.md.
They are unreleased additions, not a change to the published 0.1.0 package. Do not duplicate
them into .agents/skills or infer discovery from this inventory alone.

Use Python 3.10+ standard library. No SDK/tool installation without permission.
Run `python3 -m unittest discover -s tests -v` before committing. Add a failing regression
before a bug fix. Do not weaken validation to make a target work.

No root, firmware modification, general retail-TV shell, TLS bypass or security-policy bypass.
Device work needs explicit target/operation authorization and one exclusive device lane. Keep
leases short and release owned processes/forwards only. Never kill shared SDB servers or remove
all forwards as cleanup. Do not drive real media-account writes during UI tests.

Keep credentials, distributor DUIDs, private addresses and captured personal media out of git
and public logs. No secrets in examples, CLI diagnostics or fixtures. Public docs use placeholders.
Do not copy GPL client code into this MIT toolkit; lessons may be independently expressed as
facts with attribution. Samsung SDK binaries/templates are not redistributed.

Review three times: falsify plan assumptions; attack implementation boundaries; freeze the
tested committed tree and audit claims. Model/year support and Store approval require their own
evidence. Report blocked capture/unsupported methods rather than synthetic success.
