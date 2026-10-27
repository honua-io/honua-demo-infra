# Governed database migration runner

This isolated Terraform root owns only the no-trigger Lambda that applies the
honua-server `574c804176d969550a8e3002e0ebcc5b12df6893` pending migration set
(`Honua.Postgres.Migrations.008_AddRasterSamplingFunctions` and
`Honua.Server.Migrations.136`-`140`), its log group, execution role, and
security group. Its state key is `demo/aws-demo/db-migration-runner.tfstate`;
it never reads or writes the primary demo or candidate-preflight states. The
earlier 092-105 build was never deployed (those scripts were applied on
2026-10-22, see `runbook/rc1-upgrade-dogfood-20261022.md`).

The runner is not the serving image with migrations toggled on (honua-server's
migrate-only mode refuses to run inside Lambda). It is a fixed, reviewed
artifact holding, per script, the exact commands honua-server's own DbUp
engine executes at that commit (variables substituted, split by DbUp's
PostgreSQL splitter) and the forward-compatibility declaration the server
records beside the journal entry (honua-server#6477). `derivation/` holds the
throwaway harness that produced them from the server source and the equivalence
proof; see `runbook/db-migrations-574c804.md`. The runner accepts one event,
runs with reserved concurrency one, takes the server's advisory migration lock,
requires the journal to hold exactly the reviewed 158-script baseline (it
reports missing/unexpected names otherwise), applies and journals the six
scripts and their declarations in one transaction, and reports only an
allowlisted receipt. All six scripts classify Expand, so no contract-approval
nonce is required; a future set with a Contract script must carry the server's
nonce in the manifest and the invoke payload (`approveContractMigrations`). It
has no trigger and must be invoked only by qualified ARN after the runbook's
manual snapshot gate.

`runner/build.py` emits a fixed-timestamp, fixed-mode, sorted ZIP. The plan
operator builds it twice and requires byte equality before installing the
canonical archive that Terraform hashes directly. Artifact construction is
therefore complete before planning and cannot be deferred to apply.

Never run `terraform apply` directly. The reviewed saved-plan boundary is
`scripts/db-migration-runner-plan-apply.sh`; runtime authorization is separate.
