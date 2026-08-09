# Planned WMS release contract

`wms-release.v1.json` is the governed source for the two Maui hazard WMS
bindings and the immutable arm64 server image required to render them. While
its `admission.status` is `planned`, the generator publishes the bindings only
under `releaseCandidates.wms`; it does not add `protocols.wms` to a service.
Consumers must therefore treat the endpoints as unavailable.

Changing the release to `live` fails generation unless every binding has
`governance.status: approved`. Runtime admission additionally requires a
planned-mode canary receipt that binds the deployed image digest, full server
commit, architecture, published manifest SHA-256, exception-free
GetCapabilities, and a bounded nonblank 512x512 GetMap PNG. See
`runbook/wms-release-promotion.md` for the promotion and rollback sequence.

The current flood and sea-level-rise governance states are deliberately
`blocked`: authoritative source, portal-terms, and methodology URLs are
digest-bound, but the exact seeded artifacts have not yet been immutably bound
to source-specific redistribution grants and attribution obligations. The
free-text license cells in `SEED_MANIFEST.md` are not admission evidence.

## Additive `demo-services.v1` fields

The release adds backward-compatible fields and bumps `schemaVersion` to
`1.1.0`:

- `sources.wmsRelease`: path to the deterministic source definition.
- `releaseContracts.wms`: admission state, source definition digest, required
  server image/commit/platform/fix, and required proof names.
- `releaseCandidates.wms.bindings`: exact planned service, WMS request, map
  semantic, and blocked governance bindings. Present only while planned.
- `services[].protocols.wms`: emitted only after runtime evidence exists and
  every governance binding is approved.

The default scheduled canary probes only admitted `protocols.wms`. An
operator dispatch with `wms_admission=planned` probes release candidates and
requires the exact deployed image, commit, and architecture variables.
