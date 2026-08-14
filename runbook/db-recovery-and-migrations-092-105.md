# Governed recovery point and migrations 092-105

This procedure deploys and invokes a dedicated migrations-only artifact. It
does not toggle the serving Lambda, use `$LATEST`, publish an application
version, move `live`, seed data, or promote candidate `:40`.

## Immutable inputs

- Successful preflight receipt SHA-256: `357424246af64a7e435ac5e694f50d8935fd61744ac7ce954efb05223dc3c0ee`
- Candidate source: `7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad`
- Candidate image: `sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861`
- Live boundary: alias `live` -> `:39`, no routing
- Candidate boundary: immutable `:40`, `HONUA_SKIP_MIGRATIONS=true`
- Database: `honua-demo-demo-postgres`, PostgreSQL `15.17`, encrypted, status `available`
- Pending set: ordered 092-105, all `Expand`, digest `e0ee6b49e11639e971a58efd942f377de588b81bd6f8ed7eb0dae4ccb1a28cb7`
- Recovery point: `honua-demo-pre-092-105-e0ee6b49e116`
- Runner ZIP SHA-256: `6932540222f1e86821f2594ad4a57ffe2268753d633f8af4f7697471aea37504`

## Phase 1: deploy the runner

After merge and separate authorization, use a clean checkout at the exact
merge commit with Terraform `1.15.8`:

```bash
MERGED_SHA="$(git rev-parse HEAD)"
DEPLOYMENT_EVIDENCE_DIR="$HOME/.honua-runtime-proof/db-migration-runner-plan-$MERGED_SHA"
scripts/db-migration-runner-plan-apply.sh "$MERGED_SHA" "$DEPLOYMENT_EVIDENCE_DIR"
```

Review `runner.show.json` before authorizing the saved-plan apply. It must be
create-only and contain only the runner Lambda, role/policy, log group,
security group, and two metadata data sources. Before planning, the operator
builds the fixed-metadata ZIP twice, requires byte equality, installs that
exact canonical ZIP, and binds all three archive copies into the reviewed
artifact manifest. No build or archive data source is deferred to apply.

## Phase 2: recovery point and single invocation

This is a second authorization boundary. The operator creates the attempt
marker before `CreateDBSnapshot`, rejects any existing fixed snapshot, waits
for `available`, asserts exact source/engine/encryption/KMS/tags, and only then
invokes the immutable qualified runner once.

```bash
GOVERNANCE_SHA="$(git rev-parse HEAD)"
PREFLIGHT_EVIDENCE_DIR="$HOME/.honua-runtime-proof/candidate-preflight-v3-invocation-fd614295a5cb5ae1297930f189e92b327e1f4f0b"
scripts/db-migration-runner-invoke.sh "$GOVERNANCE_SHA" "$DEPLOYMENT_EVIDENCE_DIR" "$PREFLIGHT_EVIDENCE_DIR"
```

Any failure is terminal. Do not delete or reuse the fixed snapshot, manufacture
a new evidence directory, invoke again, or invoke the unqualified function.
Diagnose from sanitized evidence and require a new reviewed governance change.

Success requires exact ordered 092-105 application, journal continuity 001-105,
no pending or unknown migrations, and unchanged candidate `:40`, live `:39`,
preflight helper `:3`, RDS identity, runner identity, and isolated state.
