# Candidate preflight helper v3 invocation

This procedure authorizes exactly one synchronous attempt against immutable
helper `arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-candidate-preflight:3`.
It does not change or reuse the historical helper `:1` plan, apply, governance,
or invocation paths. Their local evidence directories and receipts remain
sealed and immutable.

Helper `:3` is pinned to revision `8114746a-223f-4358-a260-bd5699d7f992`,
code SHA-256 `kp5S3LTvL8L2csu0yvP9zKwUv+0RP0Q++lgnTobCYgU=`, and 5715 bytes. Its
deployment is bound to commit `e6e292bbd5e5a2e7b6477e3595fa12efdd5ecd44`,
sealed apply-evidence manifest SHA-256
`8e4e60edd3b2d066b13f1c8ae9cbb89cfb0c432d9cab72b04343d170e11f0828`,
isolated-state lineage `7e7947a0-303e-b5f3-6e03-6fa7fb4ef6a2`, and serial 4.

The operator never reads an unqualified helper or an ambiguous Terraform
current output. It derives the only allowed evidence directory as the normalized
`$HOME/.honua-runtime-proof/candidate-preflight-v3-invocation-$GOVERNANCE_SHA`
path and binds that exact resolved path into the governance receipt. Before the invocation it proves helper `:3`, candidate `:40`,
live alias `:39` with no routing, exact ECR provenance, exact IAM, the clean
governance commit, and the sealed apply manifest. It exports
`AWS_MAX_ATTEMPTS=1`, creates a terminal attempt marker before the AWS call,
uses `log-type=None`, and contains one invoke command. Any transport or semantic
failure is terminal; all feasible helper, app, alias, ECR, governance, runtime,
and result post-audits are still attempted.

A successful payload must report exactly migrations 092 through 105, all as
`Expand`, with no `Contract` branch, exact ordered pending-set digest
`e0ee6b49e11639e971a58efd942f377de588b81bd6f8ed7eb0dae4ccb1a28cb7`,
and the exact nine checks. It is preflight evidence only: it does not migrate,
seed, promote, or move the live alias.

After independent review, merge, and separate release-owner authorization, use
a clean checkout at the exact merge SHA from Git for Windows Bash:

```bash
GOVERNANCE_SHA="$(git rev-parse HEAD)"
APPLY_EVIDENCE_DIR="$HOME/.honua-runtime-proof/candidate-preflight-helper-v3-e6e292bb-plan"
test ! -e "$HOME/.honua-runtime-proof/candidate-preflight-v3-invocation-$GOVERNANCE_SHA"
scripts/candidate-preflight-v3-invoke.sh "$GOVERNANCE_SHA" "$APPLY_EVIDENCE_DIR"
```

The operator does not accept an evidence-directory argument. Never retry this
path or change `HOME` to manufacture a new evidence directory after an attempt. A
failure requires a new diagnosis, reviewed governance transition, and explicit
authorization. This procedure has no Terraform plan/apply, secret output,
database, migration, seed, promotion, or alias-mutation command.
