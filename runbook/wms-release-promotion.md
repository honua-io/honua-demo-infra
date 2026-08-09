# Maui WMS release promotion and rollback

This runbook promotes the immutable arm64 image that contains the Lambda
Skia/FreeType fix, proves real WMS pixels, and only then admits WMS into the
public demo manifest. It does not authorize a deploy by itself. Issue
`honua-io/honua-demo-infra#16` owns the approval and evidence record.

## Governed candidate

- Image digest: `sha256:e4844922ee698dd8898beff6bdfd5479e95f75be660a5e89607822fbec64d532`
- Image config digest: `sha256:ec5b732988ea9a0763203b8841034b66cfb35c6716c0ae1d2befc1562dc701c3`
- Server commit: `6ad71ac701ca709ec671afd09257217e8d17a149`
- Required fix: honua-server#3120, merge commit `8e162e46476191297b5ad03b8a523c322742d8b3`
- Platform: `linux/arm64`, Native AOT
- Release definition: `manifest/wms-release.v1.json`

The fixed image already exists in the demo account ECR repository. Do not
copy or rebuild it under another digest. The current public Lambda runs the
pre-fix `e58532138377006960516ba23333185be55040b2` image and terminates on
first Skia use with unresolved `FT_Get_BDF_Property`; GetCapabilities and
GetMap therefore return HTTP 500 until this promotion occurs.

## 1. Verify immutable image provenance

Run from an approved operator workstation. `HONUA_SERVER_REPO` must point to a
clean local honua-server checkout containing both commits.

```bash
export AWS_REGION=us-west-2
export REGISTRY=585192672263.dkr.ecr.us-west-2.amazonaws.com
export IMAGE="$REGISTRY/honua-server@sha256:e4844922ee698dd8898beff6bdfd5479e95f75be660a5e89607822fbec64d532"
export SERVER_COMMIT=6ad71ac701ca709ec671afd09257217e8d17a149
export FIX_COMMIT=8e162e46476191297b5ad03b8a523c322742d8b3

git -C "$HONUA_SERVER_REPO" merge-base --is-ancestor "$FIX_COMMIT" "$SERVER_COMMIT"
aws ecr get-login-password --region "$AWS_REGION" | \
  crane auth login "$REGISTRY" --username AWS --password-stdin
mkdir -p .artifacts/wms-promotion
crane config "$IMAGE" > .artifacts/wms-promotion/candidate-image-config.json
printf '%s  %s\n' \
  ec5b732988ea9a0763203b8841034b66cfb35c6716c0ae1d2befc1562dc701c3 \
  .artifacts/wms-promotion/candidate-image-config.json | sha256sum --check
jq -e --arg commit "$SERVER_COMMIT" '
  .os == "linux" and
  .architecture == "arm64" and
  .config.Labels["org.opencontainers.image.revision"] == $commit and
  .config.Labels["honua.runtime.profile"] == "web" and
  .config.Labels["honua.runtime.compilation"] == "native-aot"
' .artifacts/wms-promotion/candidate-image-config.json
```

Any mismatch stops the promotion. Do not retag around it.

## 2. Capture exact rollback state

```bash
cd stacks/aws
export FUNCTION_NAME="$(terraform output -raw lambda_function_name)"
export ALIAS_NAME="$(terraform output -raw lambda_alias_name)"
export PREVIOUS_VERSION="$(aws lambda get-alias \
  --region "$AWS_REGION" --function-name "$FUNCTION_NAME" --name "$ALIAS_NAME" \
  --query FunctionVersion --output text)"
export PREVIOUS_IMAGE="$(aws lambda get-function \
  --region "$AWS_REGION" --function-name "$FUNCTION_NAME" --qualifier "$PREVIOUS_VERSION" \
  --query Code.ImageUri --output text)"

mkdir -p ../../.artifacts/wms-promotion
jq -n --arg function "$FUNCTION_NAME" --arg alias "$ALIAS_NAME" \
  --arg version "$PREVIOUS_VERSION" --arg image "$PREVIOUS_IMAGE" \
  '{function:$function,alias:$alias,version:$version,image:$image}' \
  > ../../.artifacts/wms-promotion/rollback.json
```

Require the prior image to contain `@sha256:`. Stop if the alias, version, or
digest cannot be captured.

## 3. Produce and approve a saved Terraform plan

Use the normal real tfvars and remote backend. This image variable must be the
immutable candidate URI, never its mutable nightly tag.

```bash
export TF_VAR_honua_image="$IMAGE"
terraform init -input=false
terraform plan -input=false -out=wms-promotion.tfplan
terraform show wms-promotion.tfplan
```

The reviewer must confirm the plan preserves the current alias/version for
rollback, publishes a new Lambda version from the candidate digest, moves the
`live` alias only through the module, and publishes the still-planned service
manifest. Reject unrelated infrastructure changes.

After explicit change approval only:

```bash
terraform apply wms-promotion.tfplan
```

This repository change does not run that command.

## 4. Prove deployment identity and planned WMS semantics

```bash
export NEW_VERSION="$(aws lambda get-alias \
  --region "$AWS_REGION" --function-name "$FUNCTION_NAME" --name "$ALIAS_NAME" \
  --query FunctionVersion --output text)"
export NEW_IMAGE="$(aws lambda get-function \
  --region "$AWS_REGION" --function-name "$FUNCTION_NAME" --qualifier "$NEW_VERSION" \
  --query Code.ImageUri --output text)"
test "$NEW_IMAGE" = "$IMAGE"

cd ../..
HONUA_DEMO_WMS_ADMISSION=planned \
HONUA_DEMO_WMS_SERVER_IMAGE_DIGEST=sha256:e4844922ee698dd8898beff6bdfd5479e95f75be660a5e89607822fbec64d532 \
HONUA_DEMO_WMS_SERVER_COMMIT="$SERVER_COMMIT" \
HONUA_DEMO_WMS_SERVER_ARCHITECTURE=arm64 \
node scripts/live-demo-canary.mjs
```

The receipt must contain two passing capabilities checks, two passing map
checks with 512x512 dimensions and semantic pixel counts, the immutable
deployment tuple, and the SHA-256 digest of the exact published
`demo-services.v1.json` bytes.

Set the three `HONUA_DEMO_WMS_SERVER_*` repository variables to the values
read back from AWS, then dispatch the `live demo canary` workflow with
`wms_admission=planned`. Attach its receipt to issue #16. Do not set
`admission.status` to `live` if either local or GitHub evidence fails.

## 5. Resolve governance and admit WMS

Runtime proof is necessary but insufficient. For each binding, first replace
the blocked governance state with an approved review that immutably binds the
exact seeded artifact and vintage to source-specific redistribution terms and
attribution obligations. `redistributionAllowed` must become an explicit
boolean supported by that evidence.

In a separate reviewed PR:

1. Update `manifest/wms-release.v1.json` governance evidence.
2. Set `admission.status` to `live`.
3. Run the local checks below and regenerate `demo-services.v1.json`.
4. Merge only with the planned-mode receipt and governance review attached.
5. Apply the manifest-only Terraform plan.
6. Require the scheduled/default `wms_admission=live` canary to pass.

Only this transition adds `protocols.wms` to the public service entries.

## Rollback

If readiness, capabilities, map semantics, or existing service probes regress,
move the alias back to the exact captured version immediately:

```bash
cd stacks/aws
aws lambda update-alias \
  --region "$AWS_REGION" \
  --function-name "$FUNCTION_NAME" \
  --name "$ALIAS_NAME" \
  --function-version "$PREVIOUS_VERSION"
test "$(aws lambda get-alias --region "$AWS_REGION" \
  --function-name "$FUNCTION_NAME" --name "$ALIAS_NAME" \
  --query FunctionVersion --output text)" = "$PREVIOUS_VERSION"
test "$(aws lambda get-function --region "$AWS_REGION" \
  --function-name "$FUNCTION_NAME" --qualifier "$PREVIOUS_VERSION" \
  --query Code.ImageUri --output text)" = "$PREVIOUS_IMAGE"
```

Keep WMS planned, attach the failed receipt and rollback record to issue #16,
and restore the prior immutable image as Terraform desired state in a separate
approved plan. A later Terraform apply must not silently move the alias back
to the failed candidate.

## Local checks

These commands do not write to AWS or the live demo:

```bash
python3 manifest/generate-demo-services.py
python3 manifest/generate-demo-services.py --check
node --test scripts/test-wms-live-contract.mjs
node scripts/live-demo-canary.mjs
```

The final command probes only currently admitted live protocols by default;
because WMS is planned, it does not probe WMS or require deployment variables.
