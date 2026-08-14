# Governed database migration runner

This isolated Terraform root owns only the no-trigger Lambda that applies the
exact Honua Server migrations 092 through 105, its log group, execution role,
and security group. Its state key is
`demo/aws-demo/db-migration-runner.tfstate`; it never reads or writes the
primary demo or candidate-preflight states.

The runner is not the serving image with migrations toggled on. It is a fixed,
reviewed artifact containing the exact SQL from server commit
`7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad`. It accepts one event, runs with
reserved concurrency one, obtains the same advisory migration lock as Honua,
requires journal continuity 001-091, applies and journals 092-105 in one
transaction, and reports only an allowlisted receipt. It has no trigger and
must be invoked only by qualified ARN after the runbook's manual snapshot gate.

`runner/build.py` emits a fixed-timestamp, fixed-mode, sorted ZIP. The plan
operator builds it twice and requires byte equality before installing the
canonical archive that Terraform hashes directly. Artifact construction is
therefore complete before planning and cannot be deferred to apply.

Never run `terraform apply` directly. The reviewed saved-plan boundary is
`scripts/db-migration-runner-plan-apply.sh`; runtime authorization is separate.
