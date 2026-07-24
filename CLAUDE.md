# AGENTS.md

## Overview

Infrastructure-as-code for **demo.honua.io**, the live public Honua demo
environment. Extracted from honua-io/honua-iac (honua-iac#126). There is no
application code here — only Terraform (HCL), a couple of shell/Node helper
scripts, and operational docs. See `README.md` for the full picture:
directory layout, state/backend truth, the deployment model, seed data, and
the drift-plan CI setup.

## Tech Stack

- **Terraform** `>= 1.10, < 2.0` (HCL) — see `stacks/aws/versions.tf` for
  exact provider constraints.
- Providers: `hashicorp/aws`, `hashicorp/archive`, `hashicorp/random`,
  `hashicorp/null`.
- Bash scripts (`set -euo pipefail`) and one Node ESM script under
  `stacks/aws/scripts/`.
- CI: GitHub Actions (`.github/workflows/drift-plan.yml`). Default branch is
  `trunk`.

## Commands

```bash
cd stacks/aws
terraform init -backend=false && terraform validate    # no backend/credentials needed
terraform fmt -check -recursive .

terraform init      # real backend — needs AWS credentials + git access to the private honua-iac module
terraform plan
terraform apply     # only ever run deliberately, against the real account
```

## Conventions & Gotchas

- This repo is **private**; honua-iac and honua-server (which it depends on
  and references) are public. Never copy account IDs, VPC/subnet/security-
  group IDs, or other operational specifics from here into either of those.
- `module "honua"` in `stacks/aws/main.tf` is pinned to a git ref of
  honua-iac (`git::https://github.com/honua-io/honua-iac.git//…?ref=<sha>`).
  Bump it deliberately (and re-run the drift plan) when picking up upstream
  module changes — do not float it on a branch.
- `**/.terraform/` is gitignored; never commit provider plugins or state.
  State lives in S3 (see README.md) — never commit `terraform.tfvars` or any
  file holding real secret values.
- Do not run `terraform apply` (or anything that mutates AWS) against the
  real account unless explicitly asked. Read-only `plan`/`validate`/`fmt`
  are always fine; the CI drift workflow is read-only by design.

## Shared dev-environment rules (multi-agent WSL)

This machine runs many agents concurrently (**Codex + Claude**, often via agentflow with multiple tabs/agents). To prevent host lockups and lost work, every agent MUST follow these:

1. **Heavy builds/tests are throttled by a shared lock.** `dotnet` and `npm` are PATH-shimmed, so their build/test/publish/pack and ci/install/test/run-build/run-test subcommands automatically run under a global semaphore (default 1 concurrent, `HONUA_BUILD_SLOTS`). For other heavy tools, call the wrapper explicitly: `with-build-lock pytest ...`, `with-build-lock cargo build`, `with-build-lock make build`. The lock is shared across ALL of this user's processes (every Codex/Claude tab, agentflow children). Do not bypass it for compiles or test suites. Long-running servers (`dotnet run`, `npm run dev`) are intentionally NOT locked — never wrap those.

2. **Commit and push when you finish a task** so your worktree can be reclaimed. An hourly job (`honua-clean`) removes a worktree ONLY when it is clean AND fully pushed (merged, remote-gone, or idle >=2d). Dirty or unpushed worktrees are NEVER touched — but uncommitted/unpushed work blocks reclamation and is at risk if the instance is reset. Build artifacts (bin/obj and untracked node_modules) are reclaimed automatically and safely.

3. **Commit hygiene — no agent attribution.** Author every commit as the repo owner only (git identity: Mike McDougall <mike@honua.io>). Do **NOT** add any agent/tool attribution to commits: no `Co-Authored-By: Claude ...`, no `Co-Authored-By: Codex ...` (or other bot co-authors), and no "Generated with Claude Code" / "Generated with Codex" / "🤖" lines in the message or PR body. Write a plain, descriptive commit message and stop.
