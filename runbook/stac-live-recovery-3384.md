# Live STAC items/search recovery (honua-server#3384)

This is a fail-closed recovery plan for the two live failures:

- `GET https://demo.honua.io/stac/collections/90810/items?limit=2` -> 500
- `POST https://demo.honua.io/stac/search` with `{"collections":["90810"],"limit":2}` -> 500

Do not invoke the STAC seed manager yet. Migrations 092-105 are necessary,
but they do not create `honua.features`, and the managed seed correctly refuses
to recreate that relation while retained change history exists.

## Verified live boundary

Read-only AWS and database evidence captured on 2026-08-21 UTC established:

- `honua-demo-demo-honua:42`, revision
  `71e97786-9c59-4112-9fcb-6f80e82a3878`, arm64, 2048 MiB, 60 seconds
- image
  `585192672263.dkr.ecr.us-west-2.amazonaws.com/honua-server@sha256:d97baf44b17ba5b9537320281f721252ed12f1fb43bee001011d720f3ae7d622`
- server deployment revision `f897700159e2791c9468c6ca85bb4e2a3a8d8433`
- alias `live -> :42`, revision
  `f1cbb8ce-cea5-47b0-ab69-1b2c73a94ada`, no weighted routing
- `HONUA_SKIP_MIGRATIONS=true` and the control-plane target requires
  out-of-band migrations
- PostgreSQL `15.17`; `public.schema_versions` has the exact 104-name journal
  through 091 and no 092-105 entries
- `honua.features` and `honua.demo_seed_revisions` are absent
- `honua.feature_changes` contains 222,124 rows; `honua.replicas` contains 0
  rows
- the active Metadata v2 pointer is exactly `Production:52`

CloudWatch records the exact PostgreSQL failure as SQLSTATE `42P01`, relation
`honua.features` does not exist. The earliest retained matching application log
is 2026-07-25 05:47:07 UTC; the current image has the same failure.

## Ownership and recovery decision

Demo infrastructure owns the out-of-band 092-105 migration deployment, the
exact `Production` seed binding, the query-only receipt, and the live canary.
It does not own a relation-loss rebaseline algorithm.

No reviewed non-destructive reconstruction tool exists in honua-server or
honua-demo-infra. The admin materialized-feature refresh deletes and reinserts
rows in an already-existing `features` table from a live source table; it does
not create a missing canonical relation or reconcile retained history. The
pinned STAC seed explicitly states that there is no complete relation-loss
rebaseline contract and raises SQLSTATE `55000` when a missing relation has any
retained `feature_changes` or registered replicas.

No known-good retained database recovery point is available:

- there are no manual RDS snapshots;
- the only RDS/AWS Backup recovery points are automated snapshots from
  2026-08-16 through 2026-08-20, all after the first retained 42P01 on
  2026-07-25;
- backup retention is three days, the latest restorable time was
  2026-08-21 05:48:11 UTC, and AWS did not report an earliest restorable time.

The owning server decision is therefore explicit:

1. Provide a verified pre-2026-07-25 dump/snapshot and a reviewed isolated
   restore/cutover procedure that proves the recovered `honua.features`
   row-count and content digest; or
2. design and review a formal relation-loss rebaseline that accounts for the
   222,124 retained change rows and every change-feed consumer before enabling
   the seed recovery path.

Do not bypass the seed guard by creating an empty table, deleting/truncating
`feature_changes`, resetting `sync_generation`, or manufacturing a receipt.

## Governed work that can proceed

After this change is merged, use a clean checkout at the exact merge SHA.

1. Plan the main demo stack with the serving image pinned by digest and the
   normal secret-injection procedure. Review the saved plan and reject any
   unrelated application, alias, RDS, secret-value, or destructive change.
   The expected STAC changes bind the seed environment to `Production` and
   deploy the credential-reconciliation receipt fields. Applying that saved
   plan is a separate operator authorization.
2. Follow `db-recovery-and-migrations-092-105.md`. Its `plan` phase exits
   without applying. The `apply` phase requires the reviewed artifact-manifest
   digest. A second authorization creates the fixed manual recovery snapshot
   and invokes the immutable qualified migrations-only runner once. The result
   receipt must prove journal continuity through 105 and unchanged serving
   `live -> :42` identity.
3. Stop. Do not invoke `honua-demo-demo-stac-seed-manager` until one of the
   restore/rebaseline choices above has its own reviewed implementation and
   evidence contract.

## Completion gate after relation recovery

When the owner-approved relation recovery exists, capture a fresh manual RDS
snapshot and re-audit the exact serving alias/image before the one-time managed
seed invocation. The attestation must report:

- format `honua.demo.stac-seed-attestation.v1`;
- seed `demo-stac-imagery-v1` at server commit
  `1fc339a3692289e9bc4ec90ed1533c5eb22a995e` and source SHA-256
  `de33f838030b7aeced93ea7f8084ad4b45b1d76e2ae53bbcbc8d3ffc7b202687`;
- metadata environment exactly `Production` and a newly active revision;
- receipt role `honua_demo_seed_receipt` with reconciliation true;
- an exact 64-hex execution SHA-256.

Set `HONUA_DEMO_STAC_RECEIPT_ROLE_ARN` and
`HONUA_DEMO_STAC_RECEIPT_FUNCTION_NAME` from the applied Terraform outputs.
The trunk live canary then reads the query-only receipt and passes only when
its revision is still the active `Production` revision and both collection
items and collection-bound POST search return non-empty results.
