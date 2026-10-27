# Emergency rollback by PR (demo.honua.io)

**Rule:** live AWS changes only through IaC and this repository's workflows. No `aws lambda update-alias`, no console clicks, no other AWS CLI mutation. On 2026-10-24 `live` was moved 53 → 52 by hand. Nothing noticed for two days, and the re-apply on 2026-10-27 surfaced a broken audit hash chain. This runbook is the fast path that replaces the CLI.

## The one-line PR

Every apply publishes a new Lambda version. Earlier published versions stay immutable, so a rollback is pointing `live` back at one of them.

1. Find the target version: the previous `live` version in the last apply's job summary (`module.honua.aws_lambda_alias.live` `function_version` before → after), or the PR that shipped the bad change.
2. Open a PR that changes one line in `stacks/aws/demo.tfvars`:

   ```hcl
   lambda_alias_version = "52"
   ```

   Example title: `fix(demo): roll live back to 52 (<reason>)`.
3. The PR's **Terraform plan** comment must show exactly one change:
   `module.honua.aws_lambda_alias.live` `function_version: "<current>" → "52"`. Do not merge if it shows anything else; drop the extra change or roll forward instead.
4. Merge. The **Apply stacks/aws (demo)** job waits on the `demo` environment. Approve it. It applies the saved plan, then re-plans and fails unless that plan is clean. Typical time from merge to rolled back: a few minutes plus the approval.
5. Verify: `GET https://demo.honua.io/healthz/ready` returns 200 and the job summary shows `live` → `52`. Read-only checks are fine, for example `aws lambda get-alias --function-name honua-demo-demo-honua --name live`.

A pinned version freezes its environment. Leaving `lambda_alias_version` set keeps `live` on that version through later applies, even when they publish new versions. **The PR that ships the fix must remove the line** (back to `null`). Its plan then shows `live` → the new version.

## What a rollback does not undo

- Database migrations. Published versions only switch code and environment. A schema change the old version cannot read needs the migration runbooks (`db-recovery-and-migrations-092-105.md`).
- Secrets and other resources the bad change modified. Revert those in the same PR, or in a follow-up PR, through the same workflow.

## When the workflow itself cannot run

If GitHub Actions or the `demo` role is unavailable, that is break-glass. Break-glass follows honua-iac policy (honua-iac#318, scheduled after the 2026.1 RC). Until #318 lands, an out-of-band change needs the founder's explicit go, before the change, and a PR the same day that makes trunk match. The nightly drift plan (`drift-plan.yml`) stays red until it does.
