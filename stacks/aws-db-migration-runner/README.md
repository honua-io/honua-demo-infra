# Governed database migration runner

This isolated Terraform root owns only the no-trigger Lambda that applies the
honua-server `99c422646c4cd246a73e9b65efd00e9e36724587` pending migration set
(69 `Honua.Server.Migrations` scripts, 141-224) before the demo moves to that
Redis-free image. Its state key is `demo/aws-demo/db-migration-runner.tfstate`;
it never reads or writes the primary demo or candidate-preflight states. The
previous 574c804 runner (Honua.Postgres 008 and Honua.Server 136-140) was
already applied (demo readiness answers 200 on its schema) and is replaced, not
kept, by this set's apply; its
history is `runbook/db-migrations-574c804.md`.

The runner is not the serving image with migrations toggled on (honua-server's
migrate-only mode refuses to run inside Lambda). It is a fixed, reviewed
artifact holding, per script, the exact commands honua-server's own DbUp
engine executes at that commit (variables substituted, split by DbUp's
PostgreSQL splitter) and the forward-compatibility declaration the server
records beside the journal entry (honua-server#6477). `derivation/` holds the
throwaway harnesses, `emit_runner_artifacts.py` and the equivalence job that
produced and proved them; see `runbook/db-migrations-99c4226.md`. The runner accepts one event,
runs with reserved concurrency one, takes the server's advisory migration lock,
requires the journal to hold exactly the reviewed 164-script baseline (it
reports missing/unexpected names otherwise), applies and journals the 69
scripts and their declarations in one transaction, and reports only an
allowlisted receipt. One script (183, drop-index) classifies Contract, so the
manifest carries the server's one-shot approval nonce and the invoke payload
must carry the same value (`approveContractMigrations`); the operator supplies
it explicitly as `APPROVE_CONTRACT_MIGRATIONS`. It
has no trigger and must be invoked only by qualified ARN after the runbook's
manual snapshot gate.

`runner/build.py` emits a fixed-timestamp, fixed-mode, sorted ZIP. The plan
operator builds it twice and requires byte equality before installing the
canonical archive that Terraform hashes directly. Artifact construction is
therefore complete before planning and cannot be deferred to apply.

Never run `terraform apply` directly. The reviewed saved-plan boundary is
`scripts/db-migration-runner-plan-apply.sh`; runtime authorization is separate.
