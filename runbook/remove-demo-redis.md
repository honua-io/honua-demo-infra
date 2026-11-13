# Remove the demo Redis cache

Founder direction, 13 November 2026: remove `honua-demo-demo-redis` through
IaC and the normal demo deployment, without new spend or direct AWS changes.
`stacks/aws/demo.tfvars` disables both Redis flags and omits the operation
key-ring certificate ARN. Generic optional Redis resources and historical
migration addresses remain available for existing state.

## Deployment prerequisite

The committed serving image is server `574c804176d9`, published before the
Postgres durable-job changes. It supports Redis-absent startup and explicit
node-local event storage, but still composes durable process jobs from Redis.
Removing Redis with that image would lose the existing async process journey.
Use an already-published, approved Lambda AOT amd64 image with the required
Postgres-backed job substrate and Redis-free operation/key protection support.
Record its source and digest in the same deployment change. Do not select an
unverified image, disable assertions, or treat startup readiness alone as proof.
The server Redis-removal owner retains ownership of those runtime changes.

## Saved plan and normal deployment

Use `.github/workflows/terraform.yml`; no local apply or AWS CLI operations.
Review its PR plan before merge, and the protected deployment's saved plan
before human approval. Expect removal of the managed Redis replication group,
subnet group, security group, generated auth password, connection secret/version,
Redis Lambda egress and operation-key-ring IAM policy. Confirm the serving
Lambda no longer has `ConnectionStrings__redis` or
`Operations__SecretChannel__KeyRingCertificatePkcs12` in its environment.
Reject database replacement or unrelated resource changes.

Preserve the operator-owned operation-key-ring certificate secret. Terraform
never owned it; the historical `exports-redis-prereq.md` records its provenance.
Do not delete it or re-enable Redis using that historical procedure as routine
recovery. Any rollback is another reviewed configuration PR.

Merge and apply through the existing protected demo workflow after the image
prerequisite is satisfied. Require its clean convergence plan and the existing
public-demo canary (readiness, sync execution, async job/result identity and
response headers) against the deployed image. Do not claim removal is deployed
from a source diff or a plan alone.

Without Redis the application rate limiter uses process-local fixed windows;
300 requests/minute remains the configured per-subject budget. The existing
API Gateway throttles remain in place. This is not one shared window across
all Lambda execution environments.
