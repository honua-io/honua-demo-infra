# State and local data load

An operator runs **Load demo dataset** on `trunk`, through the protected
`demo` environment. It reuses the deployment role, existing object storage,
database and administrator secret. It creates no infrastructure. The role
needs `s3:ListBucket` (missing-object detection) and `cloudwatch:GetMetricStatistics` in addition to its existing permissions.

Use one immutable, merged `honua-samples` commit for the entire load:

1. Dispatch `prepare` with `samples_sha`. Leave the other inputs empty.
   Download `state-local-shared-load` and review `shared-load-plan.json`,
   `workflow.json`, source licences, counts and digests. Preparation uses no
   AWS credentials and does not import data.
2. Dispatch `stage` with that commit, the prepare run ID and the reviewed
   `plan_sha256`. Snapshots are uploaded under content-addressed keys in the
   existing storage bucket. Import uses short-lived URLs and keeps layers
   disabled. Inspect all deployment receipts and monitoring output.
3. Dispatch `activate` with the same commit and digest and the successful
   stage run ID. Every receipt is verified before enabling anonymous read
   access. Check the public sample and Console data inventory.
4. To withdraw access, dispatch `rollback` using the latest claimed run's
   artifact, commit and digest. It disables only the layers named in verified
   receipts; it never drops tables or departments.

The nine property/context layers contain approximately 57,000 features;
each prepare fetches a new snapshot, so its counts and digest are authoritative.
Display geometry is simplified in WGS84. It is unsuitable for surveying or
emergency decisions. Source attribution stays with each layer.

## Failure and retry

The workflow retains snapshots, import intents and receipts even on failure.
Never restart with a fresh prepare artifact after an import attempt. Use the
artifact whose `generation.json` names the latest claimed run. A single conditional
object-store generation marker rejects stale artifacts across all plans. A new
plan requires manual reconciliation and retirement of that marker; preparing
a fresh snapshot never authorizes another import. A failure before
claiming a generation leaves the prior generation current. If the artifact
upload failed after claiming, reconcile the generation and imported data
before any retry.

An intent without a deployment receipt means an import may have completed.
The loader refuses to import it again or roll it back automatically. Inspect
the server's import state and reconcile that intent before proceeding. A
connection or monitoring failure does not prove that a server import stopped.
Keep all artifacts until reconciliation; download them before the 90-day
retention expires. After expiry rollback is manual in Console using the saved
receipts; downloaded artifacts cannot be supplied to this workflow. Imported data and staged source objects remain after rollback.

## Capacity and credentials

Staging is sequential. The plan rejects a conservative storage reservation
above 900 MB. During loading, fresh database metrics must show fewer than
130 connections, more than 2 GB free and less than 900 MB storage growth
from the retained baseline. Missing or stale metrics stop the client. Metrics
can lag and include unrelated activity; check actual table and index sizes
against the approved 1 GB budget before activation. Rollback may disable
layers while a capacity ceiling is exceeded, but still requires fresh metrics.

Credentials and signed URLs are masked and stay outside artifacts. Child
HTTP output is suppressed because failure bodies can contain sensitive data.
Use retained receipts and the protected environment configuration to diagnose
failure. Do not paste raw AWS output or signed URLs into issues or logs.

Preparation runs without a protected environment or OIDC permissions. Only the
load job shares the apply concurrency group. Never queue multiple load dispatches:
GitHub can cancel a pending apply; inspect Terraform runs and rerun a cancelled
apply after loading. The load refuses queued or running Terraform runs.
