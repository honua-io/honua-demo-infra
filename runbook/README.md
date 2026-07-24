# Demo operator runbook

`demo-honua-io-capability-runbook.md` is the operator runbook for bringing
`demo.honua.io` to full demonstrable capability. It moved here from
honua-server `docs/internal/demo/demo-honua-io-capability-runbook.md` as part
of the honua-iac#126 extraction (demo ops docs no longer live in the
honua-server backlog).

> **PROVISIONAL IMPORT.** This is the `docs/internal/demo/…` file as it stood
> on honua-server `trunk` at commit `30d6d6f115e23e193fdaceb2a5742180165cc618`
> at extraction time. honua-server PR
> [#3009](https://github.com/honua-io/honua-server/pull/3009) ("docs: make
> Amazon Location + PrivateLink the primary geocoding fix") — runbook v2 —
> was **open, not merged** (`mergeStateStatus: BLOCKED`) after a 30-minute
> foreground poll during the extraction, so it is **not** reflected here. No
> honua-server pointer-stub PR was opened (that step is gated on #3009
> merging first — see honua-iac#126 sequencing). Follow-up: once #3009
> merges, re-pull `docs/internal/demo/demo-honua-io-capability-runbook.md`
> from honua-server trunk at the merge commit, replace this file with it, and
> only then open the honua-server stub PR that replaces the original with a
> pointer here.

No separate probe/verification scripts were moved: the runbook's verification
commands are inline `bash`/`curl` blocks within the markdown itself, not
standalone script files. The one script it references,
`tests/seed/apply-demo-stac-seed.sh`, is schema-coupled seed tooling that
stays in honua-server per the seed-data policy (see the repo-root README →
"Seed data").
