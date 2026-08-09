# Protected client-compat binding rotation

This runbook rotates the `test_service` certification binding without making
the fixture public or leaving the Python SDK pointed at mixed server, image,
layer, and credential versions. It never stores key material in Git, logs,
descriptors, or evidence.

## Governed contract

- Seed source: `stacks/aws/client-compat-seed.v1.json`
- Generated descriptor: `manifest/client-compat.v1.json`
- Access policy: `allowAnonymous:false`
- Consumer: `honua-io/honua-sdk-python`, GitHub environment `staging`
- Canary secret: `HONUA_CLIENT_COMPAT_API_KEY` in this repository
- Consumer secret: `HONUA_API_KEY` in the Python SDK `staging` environment

The descriptor records service/layer identity and the typed schema, but not a
key. A canary receipt is valid authenticated evidence only when it records a
full server commit and an image pinned by `@sha256:`.

## Prepare a rotation bundle

1. Name the demo operator, Python SDK owner, and incident/rollback owner.
2. Record the current Lambda `live` alias target, full server commit, immutable
   image digest, service/layer binding, environment variables, and secret
   version identifiers. Do not record secret values.
3. Prepare the replacement API key using the approved server/AWS secret
   rotation mechanism. Do not place it on a command line or in shell history.
4. If service or layer identity changes, update the seed definition,
   regenerate the descriptor, and pass drift checks before touching consumers.
5. Confirm the definition retains the exact fields `objectid`, `name`,
   `description`, `status`, `count`, `ratio`, `uid`, and `active`, plus the
   10-row contract. A public Maui service is not a substitute.

## Staged atomic cutover

Treat these steps as one change bundle. If any verification fails, execute
rollback rather than leaving a partially updated consumer.

1. Move the demo Lambda alias to the reviewed server version/image through the
   normal deployment approval path. Record full commit and image digest.
2. Run `stacks/aws/scripts/seed-test-service.sh`. It applies protected access
   before checking for a changed layer id, so a mismatch fails closed.
3. Rotate the server credential and set the same value in the
   `honua-demo-infra` secret `HONUA_CLIENT_COMPAT_API_KEY` and the
   `honua-sdk-python` `staging` secret `HONUA_API_KEY`. Use secret input/stdin;
   never echo the value.
4. Update the demo-infra variables `HONUA_CLIENT_COMPAT_SERVER_COMMIT` and
   `HONUA_CLIENT_COMPAT_SERVER_IMAGE` together. The image must include
   `@sha256:`.
5. Update the Python `staging` variables as one reviewed set:
   `HONUA_BASE_URL`, `HONUA_SERVICE_ID`, `HONUA_LAYER_ID`,
   `HONUA_SERVER_COMMIT`, `HONUA_SERVER_IMAGE`, `HONUA_SEED_PROFILE`, and
   `HONUA_ENABLE_WRITE_SMOKE=false`.
6. Dispatch the demo live canary. Require `anonymous-denial` and both
   authenticated metadata/query checks in one receipt.
7. Dispatch `Python SDK Staging Integration` with `local_stack=false`. Require
   the typed-field assertions and attach its run/artifact to issue #28.
8. Only after both receipts are green, mark the binding active and retire the
   prior credential according to the secret-retention policy.

## Rollback

1. Move the Lambda `live` alias back to the recorded version if it changed.
2. Restore the prior active server secret version and both environment secrets
   through the approved secret-management path.
3. Restore the complete prior Python variable set; never mix an old layer id
   with new server/image lineage.
4. Reapply `allowAnonymous:false` and rerun the anonymous-denial canary.
5. Record the failed binding, rollback owner, timestamps, and receipt URLs on
   issue #28. Keep it open until authenticated and Python staging evidence is
   green.

## Local checks

These commands do not access AWS or secrets:

```bash
python3 manifest/generate-client-compat.py
python3 manifest/generate-client-compat.py --check
node --test scripts/test-client-compat-canary.mjs
```

Running the seed script, changing the Lambda alias, rotating AWS/server
secrets, changing GitHub environment state, and dispatching live canaries are
external operator actions and are intentionally not performed by this change.
