# Governed recovery point and migrations 092-105

This procedure deploys and invokes a dedicated migrations-only artifact. It
does not toggle the serving Lambda, use `$LATEST`, publish an application
version, move `live`, or seed data. It is a prerequisite for, not a complete
fix for, the live STAC recovery in `stac-live-recovery-3384.md`.

## Immutable inputs

- Migration-source preflight receipt SHA-256: `357424246af64a7e435ac5e694f50d8935fd61744ac7ce954efb05223dc3c0ee`
- Migration source: `7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad`, image `sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861`
- Serving boundary: immutable `honua-demo-demo-honua:42`, revision `71e97786-9c59-4112-9fcb-6f80e82a3878`, image `sha256:d97baf44b17ba5b9537320281f721252ed12f1fb43bee001011d720f3ae7d622`
- Alias boundary: `live` -> `:42`, revision `f1cbb8ce-cea5-47b0-ab69-1b2c73a94ada`, no routing
- Serving migration contract: `HONUA_SKIP_MIGRATIONS=true` and `RequiresOutOfBandMigrations=true`
- Database: `honua-demo-demo-postgres`, PostgreSQL `15.17`, encrypted, status `available`
- Pending set: ordered 092-105, all `Expand`, digest `e0ee6b49e11639e971a58efd942f377de588b81bd6f8ed7eb0dae4ccb1a28cb7`
- Recovery point: `honua-demo-pre-092-105-e0ee6b49e116`
- Runner ZIP SHA-256: `ec7b6775eb02cf37a73bd7053e6da2a524be343b7785e6384b43b3a708b2c79a`
- Expected AWS `Configuration.CodeSha256`: `7HtndesCzzenO9cFPm2ipSS+NDt3heY4S0Ozpwiyx5o=`
- Exact ordered 104-name pre-092 journal SHA-256: `8a49e1c886f6ddf58f4baf89f7b74bdfcde3fbea4d2555050d56a050f78a15c4`

## Phase 1: deploy the runner

After merge and separate authorization, use a clean checkout at the exact
merge commit with Terraform `1.15.8`:

```bash
MERGED_SHA="$(git rev-parse HEAD)"
DEPLOYMENT_EVIDENCE_DIR="$HOME/.honua-runtime-proof/db-migration-runner-plan-$MERGED_SHA"
scripts/db-migration-runner-plan-apply.sh plan "$MERGED_SHA" "$DEPLOYMENT_EVIDENCE_DIR"
```

Review `runner.show.json` before authorizing the saved-plan apply. It must be
create-only and contain only the runner Lambda, role/policy, log group,
security group, and two metadata data sources. Before planning, the operator
builds the fixed-metadata ZIP twice, requires byte equality, installs that
exact canonical ZIP, and binds all three archive copies into the reviewed
artifact manifest. Members use uncompressed `ZIP_STORED` bytes and all hashed
repository inputs are pinned to LF checkouts and rejected byte-for-byte if any
CR occurs. ZIP member order is based on UTF-8 POSIX member names, not host path
semantics, for cross-host reconstruction.
CI independently rebuilds twice and requires the same advertised digest. No
build or archive data source is deferred to apply. Planning exits without
applying. After an independent reviewer approves the exact show JSON and
manifest, pass the digest of the reviewed manifest back across the phase
boundary:

```bash
REVIEWED_MANIFEST_SHA256="$(sha256sum "$DEPLOYMENT_EVIDENCE_DIR/reviewed-artifacts.sha256" | cut -d' ' -f1)"
scripts/db-migration-runner-plan-apply.sh apply \
  "$MERGED_SHA" "$DEPLOYMENT_EVIDENCE_DIR" "$REVIEWED_MANIFEST_SHA256"
```

## Phase 2: recovery point and single invocation

This is a second authorization boundary. The operator creates the attempt
marker before `CreateDBSnapshot`, rejects any existing fixed snapshot, waits
for `available`, asserts exact source/engine/encryption/KMS/tags, and only then
invokes the immutable qualified runner once.

```bash
GOVERNANCE_SHA="$(git rev-parse HEAD)"
scripts/db-migration-runner-invoke.sh "$GOVERNANCE_SHA" "$DEPLOYMENT_EVIDENCE_DIR"
```

Any failure is terminal. Do not delete or reuse the fixed snapshot, manufacture
a new evidence directory, invoke again, or invoke the unqualified function.
Diagnose from sanitized evidence and require a new reviewed governance change.

The qualified runner performs the database preflight inside its single
transaction before executing SQL: the journal must be the exact ordered
104-name baseline through 091 with no 092-105 replay. The verified manual RDS
snapshot exists and is `available` before that invocation. Success requires
exact ordered 092-105 application, journal continuity 001-105, no pending or
unknown migrations, and unchanged serving version `:42`, `live -> :42`, RDS
identity, runner identity, and isolated state.
Raw `get-function` responses are never persisted: the operator validates the
exact runner code/configuration and serving identities in a pipeline,
then writes only allowlisted sanitized evidence. Pre/post receipts bind every
artifact digest, including the qualified runner `Configuration.CodeSha256`.
The runner sanitizer derives the exact security-group ID and DB-secret ARN from
the isolated postapply state's exact seven-node graph, proves both the state
Lambda and deployed Lambda reference them, and receipt-binds those safe IDs.
