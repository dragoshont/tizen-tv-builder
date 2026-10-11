---
name: samsung-tv-certificates
description: Prepare Samsung TV author/distributor signing profiles with the target DUID and diagnose certificate or install-permit failures. Not for harvesting identifiers, account sign-in automation or Store approval.
---

# Author identity and device distribution are separate

Resolve the toolkit at `../..` from this skill's reported base directory.
Read [Common contract](../common.md) for permission and privacy holds, then
[certificate steps](../../docs/getting-started.md#samsung-tv-certificates).

1. With explicit owner permission, open installed Certificate Manager and add
   **Samsung → TV**, not a generic Tizen profile. Human account sign-in stays
   with the owner; never extract browser sessions.
2. Create or reuse the application's author certificate. Preserve that identity
   and package/app IDs for updates; replacing the author can invalidate updates.
3. Create the distributor certificate containing the intended TV's DUID.
   Obtain it through Samsung's owner-facing UI/Device Manager; retain it privately.
   Another TV requires its own DUID authorization and distributor coverage.
4. Keep `.p12`, passwords, certificate XML and DUIDs outside source, packages,
   fixtures and public diagnostics. The skill never materializes secrets.
5. Select the named profile and verify it in Certificate Manager. Close that
   manager before the coordinated SDK run; its SDB ownership interfered upstream.
6. Under authorization and the device lane, use Device Manager's **Permit to
   install applications**, or the guide's explicit `install-permit` command.
   This is a separate mutation, not implied by signing or connection consent.

For error 118, check install permission first alongside profile, target DUID,
expiry and manifest identity. Upstream permission fixed that error on one set;
it is not a universal error-code diagnosis or justification for security bypass.
Distinguish SDK package success, signature-file presence, cryptographic/provenance
verification and actual developer installation. None establishes Store approval.

Evidence: [Samsung certificate guide](https://developer.samsung.com/smarttv/develop/getting-started/setting-up-sdk/creating-certificates.html),
[observed install-permit failure](../../docs/lessons.md).
