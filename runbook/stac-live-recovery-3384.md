# Live STAC items/search recovery (honua-server#3384)

This is the fail-closed recovery procedure for the two live failures:

- `GET https://demo.honua.io/stac/collections/90810/items?limit=2` -> 500
- `POST https://demo.honua.io/stac/search` with `{"collections":["90810"],"limit":2}` -> 500

## Owner decision: RESTORE

Operator ruling A (2026-09-16) on honua-io/honua-demo-infra#79 selected
**RESTORE**: recover `honua.features` from the last good copy with a cutover
that proves the row count and content digest, then run migrations 092-105
through the governed runner, then run the managed seed.

The relation-loss rebaseline path is **deferred to 2026.2** as a design item.
It is not part of the 2026.1 recovery, and nothing in this runbook implements
it.

Do not invoke the STAC seed manager yet. It runs only at step 10 below, after
the restore cutover proof and migrations 092-105 have both passed.

## Verified live boundary

Read-only AWS and database evidence captured on 2026-08-21 UTC and re-read on
2026-09-16 UTC established:

- `honua-demo-demo-honua:42`, revision
  `71e97786-9c59-4112-9fcb-6f80e82a3878`, arm64, 2048 MiB, 60 seconds
- image `honua-server@sha256:d97baf44b17ba5b9537320281f721252ed12f1fb43bee001011d720f3ae7d622`
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
is 2026-07-25 05:47:07 UTC; the current image has the same failure. Migration
089 was applied at 2026-07-24 23:34:58 UTC, immediately before that first error.

## Restore source

### What does not exist

No database-level backup predates the first 42P01:

- there are no manual RDS snapshots of `honua-demo-demo-postgres`;
- the only RDS recovery points are automated snapshots from 2026-09-12 through
  2026-09-16 (07:25 UTC daily), and the point-in-time window is
  2026-09-13T18:46Z to 2026-09-16T18:46Z; backup retention is three days;
- no AWS Backup vault exists, no RDS snapshot export task exists, and the demo
  data bucket (unversioned) holds only import sources, tiles, fonts and
  manifests, with no `pg_dump` artifact;
- the postgis-bootstrap handler has no export operation.

### The last good copy: `public.features`

The feature rows were never deleted. Migration 001 creates the unqualified
`features` table and the migration runner connects with `search_path=public`,
so the relation, its 13 `idx_features_*` indexes, its owned sequence
`public.features_objectid_seq` and `trigger_track_feature_changes` all live in
`public`. The serving image qualifies the relation with its default metadata
schema (`honua`) and so cannot see it.

Read-only proof captured on 2026-09-16 UTC through
`scripts/stac-features-restore.py prove pre-cutover` (query-only event):

| Evidence | Value |
|---|---|
| Restore source relation | `public.features` in `honua-demo-demo-postgres` |
| Rows | 110,229 |
| Content SHA-256 (all rows) | `12cf791316b8739de47844f2dc1724879f4514570879cf4508ec32902297521e` |
| Key-set SHA-256 (`layer_id\|objectid`) | `573a62ff64d5c8784566f95800cfcaba771fa247699bbfcf5ffd93ff6ec7a24c` |
| Non-seed rows / content SHA-256 (all layers except 90810, 90820) | 110,222 / `fce37c30965c5aa96cd46188e5b993755dd72c29a695a2b6e12db207acd3bf91` |
| Journal rows / max generation | 222,124 / 222,124 |
| Last journal change | 2026-07-20T21:28:14.467807Z (before the first 42P01) |
| Journal net-live key-set SHA-256 | `573a62ff64d5c8784566f95800cfcaba771fa247699bbfcf5ffd93ff6ec7a24c` |
| Replicas | 0 |
| Retained snapshot holding this relation | `rds:honua-demo-demo-postgres-2026-09-16-07-25`, created 2026-09-16T07:25:10.936Z |
| Manual copy made before cutover | `honua-demo-stac-3384-features-12cf791316b8` |

Per-layer rows: 1=51,245, 2=3,274, 3=7,071, 4=2,014, 5=1,537, 6=2,328, 7=1,
8=1, 9=1, 12=28, 13=42,674, 68810=4, 68820=3, 68821=2, 68822=29, 68823=10,
90810=4, 90820=3. The published `maui_*` tables match layers 1-6 and 13
exactly.

### Proof method

The pre-loss evidence is the append-only change journal. Every insert, update
and delete on the relation went through `trigger_track_feature_changes` until
the last write on 2026-07-20. The net-live set is the last operation per
`(layer_id, objectid)` that is not a delete. It must equal the relation's key
set exactly: same count (110,229) and same SHA-256. Zero keys are missing, zero
are untracked, and zero deleted keys are present.

The content digest is SHA-256 over rows ordered by `(layer_id, objectid)`: layer, objectid,
`md5(ST_AsEWKB(geometry))`, `md5(attributes::text)`, and UTC microsecond
`created_at`/`updated_at`. It was reproduced byte-identically on two separate
invocations, and it is pinned in the script, which re-asserts it inside the
cutover transaction.

The 222,124 retained change rows are preserved unchanged by the restore; no
journal row is written, deleted or rebased.

## Restore cutover

`scripts/stac-features-restore.py cutover` is the only data-changing step this
runbook adds. It refuses unless all of these hold:

- the pre-cutover proof file matches the reviewed SHA-256 and passed;
- `honua-demo-stac-3384-features-12cf791316b8` is an `available` manual
  snapshot of `honua-demo-demo-postgres`;
- no earlier cutover attempt marker exists in the evidence directory.

It then invokes the break-glass helper once with a single `DO` block. In one
transaction the block:

1. takes the change-writer generation lock `pg_advisory_xact_lock(144047712, 0)`
   with a 5 s lock timeout;
2. refuses if `honua.features` or `honua.features_objectid_seq` already exists,
   or if `public.features` is missing;
3. locks `public.features` exclusively and the journal/replicas in share mode;
4. recomputes the row count, content digest, key digest, journal row count,
   max generation, journal net-live key digest and replica count. It raises
   SQLSTATE `55000` unless every value equals the reviewed pins;
5. runs `ALTER TABLE public.features SET SCHEMA honua`, a zero-copy move of the
   rows, indexes, owned sequence and tracking trigger;
6. recomputes the row count and content digest on `honua.features`. It also
   asserts that `public.features` is gone, the owned sequence is
   `honua.features_objectid_seq`, and the trigger is attached. Any mismatch
   raises and rolls the whole move back.

The helper then returns the post-cutover proof row, which must pass
`post-cutover` verification. The cutover never runs `DROP`, `TRUNCATE`,
`DELETE`, `INSERT` or `UPDATE`.

If a later step shows a regression that the cutover caused, the rollback is the
reverse metadata move `ALTER TABLE honua.features SET SCHEMA public`. That is a
separate, reviewed break-glass authorization. The manual snapshot is the
disaster fallback, not the rollback.

## Rebaseline (deferred to 2026.2)

The pinned STAC seed states that there is no complete relation-loss
rebaseline contract and raises SQLSTATE `55000` when a missing relation has any
retained `feature_changes` or registered replicas. The restore makes
`honua.features` present, so the seed takes its healthy path and that guard
never fires.

Do not bypass the seed guard by creating an empty table, deleting/truncating
`feature_changes`, resetting `sync_generation`, or manufacturing a receipt. If
the pre-cutover proof ever fails, stop and escalate. Do not substitute a
rebaseline.

## Coordinator procedure

Every step runs from a clean checkout at the exact merge SHA of this change.
Steps marked **[READ-ONLY]** may run at any time. Every step marked **[APPLY]**
is a separate coordinator authorization: post its plan summary on
honua-io/honua-demo-infra#79 before running it. Stop on any failure. Never
retry a data-changing step with a new evidence directory.

```bash
MERGED_SHA="$(git rev-parse HEAD)"
RESTORE_EVIDENCE_DIR="$HOME/.honua-runtime-proof/stac-3384-restore-$MERGED_SHA"
```

1. **[READ-ONLY]** Prove the restore source and publish the proof digest:

   ```bash
   python3 scripts/stac-features-restore.py prove pre-cutover "$RESTORE_EVIDENCE_DIR"
   ```

   It must print `pre-cutover proof: PASS`. The reviewer records the printed
   `sha256` of `pre-cutover-proof.json` on #79.
2. **[APPLY: snapshot copy]** Preserve the retained snapshot before its
   automated expiry:

   ```bash
   aws rds copy-db-snapshot \
     --source-db-snapshot-identifier rds:honua-demo-demo-postgres-2026-09-16-07-25 \
     --target-db-snapshot-identifier honua-demo-stac-3384-features-12cf791316b8 \
     --tags Key=HonuaRecovery,Value=stac-3384 \
            Key=FeaturesContentSha256,Value=12cf791316b8739de47844f2dc1724879f4514570879cf4508ec32902297521e
   aws rds wait db-snapshot-available \
     --db-snapshot-identifier honua-demo-stac-3384-features-12cf791316b8
   ```

3. **[APPLY: restore cutover]** Move the proven relation into `honua`:

   ```bash
   python3 scripts/stac-features-restore.py cutover \
     "$RESTORE_EVIDENCE_DIR" "$REVIEWED_PRE_CUTOVER_PROOF_SHA256"
   ```

   It must print `post-cutover proof: PASS`.
4. **[READ-ONLY]** Re-probe the two public STAC requests and the live canary's
   FeatureServer and MVT probes. Items and search are expected to answer 200
   from the restored rows.
5. **[APPLY: main stack]** Plan the main demo stack with the serving image
   pinned by digest and the normal secret-injection procedure. Review the saved
   plan and reject any unrelated application, alias, RDS, secret-value, or
   destructive change. The expected STAC changes bind the seed environment to
   `Production` and deploy the credential-reconciliation receipt fields. Post
   the plan summary, then apply that saved plan.
6. **[APPLY: migration runner]** Follow Phase 1 of
   `db-recovery-and-migrations-092-105.md`. The `plan` phase exits without
   applying. Post its `runner.show.json` summary, then run `apply` with the
   reviewed artifact-manifest digest.
7. **[APPLY: migrations 092-105]** Follow Phase 2 of the same runbook: create
   the fixed recovery snapshot `honua-demo-pre-092-105-e0ee6b49e116` and invoke
   the immutable qualified runner once. The receipt must prove journal
   continuity through 105 and unchanged serving `live -> :42` identity.
8. **[READ-ONLY]** Prove the relation survived the migrations unchanged:

   ```bash
   python3 scripts/stac-features-restore.py prove post-migrations "$RESTORE_EVIDENCE_DIR"
   ```

9. **[DECISION]** Confirm the seed pin. `manifest.sources.stacSeed` pins server
   `1fc339a3692289e9bc4ec90ed1533c5eb22a995e`. honua-server#4808
   (`f23f5a67012502f47e307715998c81578599774a`) fixes a bootstrap defect that
   only affects environments with no activated snapshot. The live `Production:52`
   pointer is activated. Re-pinning is a separate manifest change.
10. **[APPLY: seed]** Capture a fresh manual RDS snapshot, re-audit the exact
    serving alias/image, and invoke `honua-demo-demo-stac-seed-manager` once
    as described in `demo-honua-io-capability-runbook.md`. The attestation
    must satisfy the completion gate below.
11. **[READ-ONLY]** Prove the seed changed only its own fixture layers:

    ```bash
    python3 scripts/stac-features-restore.py prove post-seed "$RESTORE_EVIDENCE_DIR"
    ```

    The key set, per-layer counts, and the non-seed content digest must be
    unchanged, and the journal must only have grown.
12. **[APPLY: repository variables]** Set
    `HONUA_DEMO_STAC_RECEIPT_ROLE_ARN` and
    `HONUA_DEMO_STAC_RECEIPT_FUNCTION_NAME` from the applied Terraform outputs,
    then run the trunk live canary. It must report 29/29 with the receipt bound.

## Completion gate after relation recovery

The seed attestation must report:

- format `honua.demo.stac-seed-attestation.v1`;
- seed `demo-stac-imagery-v1` at server commit
  `1fc339a3692289e9bc4ec90ed1533c5eb22a995e` and source SHA-256
  `de33f838030b7aeced93ea7f8084ad4b45b1d76e2ae53bbcbc8d3ffc7b202687`
  (or the re-pinned commit and digest from step 9);
- metadata environment exactly `Production` and a newly active revision;
- receipt role `honua_demo_seed_receipt` with reconciliation true;
- an exact 64-hex execution SHA-256.

The trunk live canary then reads the query-only receipt. It passes only when
the receipt's revision is still the active `Production` revision and both
collection items and collection-bound POST search return non-empty results.

## Execution record

Each **[APPLY]** step is recorded here once it has run. The full outputs are on
honua-io/honua-demo-infra#79. The evidence files live under
`~/.honua-runtime-proof/stac-3384-restore/lane-proof-20260916T190724Z` on the
executing workstation.

The coordinator approved steps 2–3 of the procedure above (plan-summary steps
3–4) at PR head `25678c7` before merge. The cutover therefore ran against the
evidence directory holding the reviewed pre-cutover proof, not a
merge-SHA-named directory. The one-attempt marker lives in that directory.

### Snapshot copy: 2026-09-16T19:54:25Z, available 19:55:55Z

- The source `rds:honua-demo-demo-postgres-2026-09-16-07-25` was copied to
  `honua-demo-stac-3384-features-12cf791316b8`: a manual, encrypted copy of
  `honua-demo-demo-postgres`, created 2026-09-16T19:55:38.923Z.
- Tags: `HonuaRecovery=stac-3384` and
  `FeaturesContentSha256=12cf791316b8739de47844f2dc1724879f4514570879cf4508ec32902297521e`.

### Restore cutover (attempt 1): 2026-09-16T19:56:52Z to 19:57:07Z

- One `cutover` invocation printed
  `post-cutover proof: PASS sha256=2681a6dbfd7a4c208a19e597703a367e02bb01968cf6b3f0ed93fece49606c85`.
- `honua.features` now holds 110,229 rows with content SHA-256
  `12cf791316b8739de47844f2dc1724879f4514570879cf4508ec32902297521e`.
- Its key set equals the journal net-live set
  (`573a62ff64d5c8784566f95800cfcaba771fa247699bbfcf5ffd93ff6ec7a24c`).
- The journal is unchanged at 222,124 rows. Replicas are 0.
- The owned sequence is `honua.features_objectid_seq`, and the tracking trigger
  is attached.

| Evidence file | SHA-256 |
|---|---|
| `pre-cutover-proof.json` | `3b8e014e4396b9324479d7a8779ed05de2bb5b87f1902181ba6c4f786144c26c` |
| `cutover-attempt.json` | `56d5cdc6500be72744ea99a2e53fb791682c80d60a992112b13d8a8f1cddc8be` |
| `cutover-event.json` | `ee83e3bfd29adb1d87b638ea51b503ec71a22b5ea91ab37482ec43ab5dcc923a` |
| `cutover-response.json` | `12aaa79db49ee3bdf2da947e946037a7c16d16def9184bcc44bc74ddf51e15c7` |
| `post-cutover-proof.json` | `2681a6dbfd7a4c208a19e597703a367e02bb01968cf6b3f0ed93fece49606c85` |

### Serving re-probe (read-only): 2026-09-16T19:57Z

On serving `live -> :42`:

- `GET /stac/collections/90810/items?limit=2` returned 500 before the cutover
  and 200 after it. Correlation id
  `00-6aaaf4a2544a91523aefd20e521f4193-61930eaee8c36cf8-00`; 2 of 4 matched.
- `POST /stac/search` returned 500 before the cutover and 200 after it.
  Correlation id `00-6aaaf4a3495a44a9196cc61b14c0f9ce-31df31ea296c7468-00`;
  2 of 4 matched.
- The unbound live canary passed 29/29.

Steps 5 onward (main stack, migration runner, migrations 092-105, seed, and the
receipt variables) have not run.
