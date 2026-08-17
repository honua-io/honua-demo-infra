# Demo operator runbook

`demo-honua-io-capability-runbook.md` is the operator runbook for bringing
`demo.honua.io` to full demonstrable capability. It moved here from
honua-server `docs/internal/demo/demo-honua-io-capability-runbook.md` as part
of the honua-iac#126 extraction (demo ops docs no longer live in the
honua-server backlog).

> **Reconciled 2026-07-24.** This is the final runbook from honua-server
> `trunk` at commit `776d74a28` — includes runbook v2 (honua-server#3009,
> Amazon Location + PrivateLink as the primary geocoding fix, merged via the
> train). The honua-server original has been replaced with a pointer stub.
> Applied-state notes beyond the runbook: geocoding infra (place index +
> `geo.places` endpoint) is live and serving is on v35 — see
> stacks/aws/vpc-endpoints.tf and honua-demo-infra#11 for the remaining
> enablement items (Redis stays off pending honua-server#3011; geocoding
> e2e pending the next image with the AOT config-binding fix).

## Demo B (ops champion) runbooks

`demo-b-ops-runbook.md` (recording runbook for the ops-champion story) and
`demo-b-safe-rollback.md` (the flagship Beat 8 safe layer-evolution /
DB-inclusive rollback sequence), with their helper scripts
`scripts/demo-b-probes.sh` (read-only live probes against demo.honua.io) and
`scripts/demo-b-safe-rollback.sh` (drives the safe-rollback loop against a
running server). Imported from honua-server `docs/internal/demo/` at trunk
`5814d1099` as part of the demo backlog/code alignment (honua-demo-infra#17 is the
transferred demo-capability program epic); the honua-server originals are
replaced with a pointer stub, and the one code reference
(`MetadataReleaseOperationOptions` XML docs) now points here.

## Feature streaming

`streaming-snapshot-conformance.md` is the operator procedure for making
baseline snapshot subscriptions serve and for enabling the controlled-
conformance mutation surface that honua-io/honua-sdk-js#818 leases
(honua-io/honua-server#3181). It records the evidence that the reported HTTP
500 is manufactured by the buffered ~6 MB gateway response rather than emitted
by the server, the triage rule that distinguishes the two, and the ordered
remediation: redeploy onto a build carrying honua-server#3206/#3038, apply the
explicit snapshot payload budget (`stacks/aws/streaming.tf`), provision a
dedicated small conformance source, and decide the admin-scoped credential the
mutating routes require. The remaining blocker for live-mutation evidence is
honua-io/honua-demo-infra#67.

## Seed scripts

## Candidate preflight

`candidate-preflight-v1.md` preserves the historical helper `:1` deployment
and invocation record. `candidate-preflight-v2.md` is the additive, qualified
helper `:2` one-attempt governance procedure; it does not repoint the v1 path.
`candidate-preflight-v3.md` independently governs the immutable helper `:3`
attempt and preserves both earlier operator paths and evidence.

`db-recovery-and-migrations-092-105.md` governs the separate manual RDS
recovery point and one-attempt, immutable, migrations-only 092-105 runner.
It never toggles or invokes an application `$LATEST`, and it leaves candidate
`:40`, live `:39`, and preflight helper `:3` unchanged.

For the capability runbook, no separate probe/verification scripts were moved:
its verification commands are inline `bash`/`curl` blocks within the markdown
itself, not standalone script files. The one script it references,
`tests/seed/apply-demo-stac-seed.sh`, is schema-coupled seed tooling that
stays in honua-server per the seed-data policy (see the repo-root README →
"Seed data") — pinned reference:

```
https://raw.githubusercontent.com/honua-io/honua-server/1fc339a3692289e9bc4ec90ed1533c5eb22a995e/tests/seed/apply-demo-stac-seed.sh
```

The sibling STAC SQL at this ref has exact SHA-256
`de33f838030b7aeced93ea7f8084ad4b45b1d76e2ae53bbcbc8d3ffc7b202687`.

(and the sibling `tests/seed/demo-*.sql` / `client-compat-v1.sql` fixtures at
the same ref). Bump the pinned commit deliberately when a newer seed fixture is
needed — do not float on `trunk`.
