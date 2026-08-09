# Client-compat evidence contract

The checked-in `manifest/client-compat.v1.json` is deterministic source truth for the
fixture identity, protected access policy, and typed schema. Deployment values do not
belong in that generated file because commits, images, timestamps, and expiries change
without changing the seed contract.

Each authenticated live canary emits `client-compat-deployment.v1.json` beside the
canary receipt. The deployment document contains the full server commit, immutable
image digest, service/layer/seed binding, commit-pinned descriptor URL and digest,
generation and expiry timestamps, and owning repository. Its canonical SHA-256 is
recorded by `client-compat-canary.v1.json`. Neither document records credentials.

## Python staging synchronization

`honua-sdk-python` consumes the complete deployment document through the single,
non-secret `HONUA_CLIENT_COMPAT_BINDING_JSON` staging environment variable. Updating
one variable prevents a mixed commit/image/descriptor/time binding.

After a successful trunk canary:

1. Download the `live-demo-canary` artifact from the terminal trunk run.
2. Verify the artifact digest and the deployment-document SHA-256 against the canary receipt.
3. Set the Python staging variable from the exact deployment document:

   ```bash
   gh variable set HONUA_CLIENT_COMPAT_BINDING_JSON \
     --repo honua-io/honua-sdk-python \
     --env staging \
     --body "$(jq -c . client-compat-deployment.v1.json)"
   ```

4. Dispatch `Python SDK Staging Integration` and require `local_stack=false`, the same
   computed deployment-evidence digest, the same descriptor URL/digest, and the same
   server/image/service/layer/seed binding in `staging-smoke-results.json`.

The Python lane fails closed when the variable is absent in remote mode, expired, has
an invalid immutable lineage, disagrees with its staging variables, or its pinned
descriptor bytes do not match the declared digest and protected fixture contract.

The incident owner is `honua-io/honua-demo-infra` via issue
`https://github.com/honua-io/honua-demo-infra/issues/28`. Roll back by restoring the
previous complete non-secret binding variable; never copy or rotate the API key as part
of evidence-only synchronization.
