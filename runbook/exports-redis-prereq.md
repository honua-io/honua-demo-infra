# Exports need Redis: key-ring certificate prerequisite (demo.honua.io)

Status 2026-10-24: **blocked on one operator-created secret.** Nothing in this
file has been applied. The rest of the stack (PMTiles, licensing, CORS, pool caps) was
applied the same day (`foss4g-demo-apply-20261024.md`), so step 4's expected plan now
contains only the Redis flip.

## Why exports are empty

`/rest/services/<id>/MapServer/export` and `/ImageServer/exportImage` answer
`200 image/png` with 0 bytes (`f=json` says 503 "Temporary export storage is
currently at capacity"). The serving Lambda logs, for every export:

    Honua.Infrastructure.Services.CloudBackedTemporaryFileService
    EventId 48370743 SharedWriteRejectedRedisRequired
    "Rejecting shared temporary file write because Redis coordination is required for cloud-backed storage quotas."

Cloud-backed temp storage (the `FileStorage__Provider=AwsS3` bucket) shares its
quota through Redis. The demo has Redis provisioned but **unwired**
(`wire_redis_connection = false`, `redis.tf`), because a Redis-connected
Production server composes the durable operation secret channel and refuses to
start without its key-ring certificate (honua-server#6500; on rc.1 the
refusal is clean since #6503). Wiring Redis also closes honua-server#6487
(admin API keys per process while Redis is off).

The certificate does not exist in the demo account. Checked read-only on
2026-10-24 with `aws secretsmanager list-secrets` in us-west-2 and us-east-1:
the demo has `admin-password`, `connection-encryption-master-key`,
`connection-string`, `license`, `license-pro`, `license-signing-key`,
`redis-connection`; no operation key-ring secret (only the unrelated
`honua-cert-cert/*` and `honua-release/cert/*` certification cells have one,
and those must not be shared: a key ring is bound to one certificate for its
lifetime).

## What the server needs (rc.1, 199bce7)

`Operations__SecretChannel__KeyRingCertificatePkcs12 = aws:secretsmanager:<arn>`.
At startup `StartupConfigurationHelpers.EnsureKeyRingCertificateMaterializedAsync`
resolves the reference with the function role, writes the PKCS#12 to a private
temp file (mode 0600) and loads it. The secret value is base64 PKCS#12 with an
RSA private key, or the JSON bundle `{"pkcs12":"<base64>","password":"<pw>"}`.
The stack already carries the inert plumbing (variable
`operation_key_ring_certificate_secret_arn`, `local.operation_key_ring_environment`
and `aws_iam_role_policy.lambda_operation_key_ring` in `redis.tf`); none of it
renders while `wire_redis_connection = false`.

## Operator steps

1. **Generate the certificate offline** (operator workstation, not this repo,
   not CI). Keep the same certificate for the life of the Redis key ring; a new
   one cannot decrypt the old ring.

       umask 077; d=$(mktemp -d); cd "$d"
       openssl req -x509 -newkey rsa:3072 -sha256 -days 3650 -nodes \
         -subj "/CN=honua-demo-operation-key-ring" -keyout kr.key -out kr.crt
       export PW=$(openssl rand -base64 24)
       openssl pkcs12 -export -inkey kr.key -in kr.crt -out kr.pfx -passout env:PW \
         -keypbe AES-256-CBC -certpbe AES-256-CBC -macalg sha256
       python3 -c 'import base64,json,os;print(json.dumps({"pkcs12":base64.b64encode(open("kr.pfx","rb").read()).decode(),"password":os.environ["PW"]}))' > bundle.json
       unset PW

   Store `bundle.json` in the operator `pass` store (honua/terraform) as the
   recovery copy before step 2.

2. **Create the secret** (the one AWS write in this procedure; it does not
   touch the serving function):

       aws secretsmanager create-secret --region us-west-2 \
         --name honua-demo-demo/operation-key-ring \
         --description "Redis operation key-ring PKCS#12 for demo.honua.io (honua-server#6500). Operator-owned; not in Terraform." \
         --secret-string file://bundle.json --query ARN --output text
       shred -u kr.key kr.pfx bundle.json; cd - >/dev/null; rmdir "$d"

   Record the printed ARN. Default `aws/secretsmanager` encryption is fine (the
   Lambda role needs no extra KMS grant for the AWS-managed key).

3. **Apply the flip diff below** in a reviewed PR, putting the ARN from step 2
   in `demo.tfvars`. Also update the `wire_redis_connection` comment in
   `demo.tfvars` and the header comment in `redis.tf`, which describe the
   unwired state.

4. **Plan and review** (read-only; same invocation as the README):

       terraform plan -input=false -lock=false -var-file=demo.tfvars -out=redis-wire.tfplan
       terraform show -json redis-wire.tfplan | python3 ../../scripts/check-lambda-env-budget.py

   Expected on top of whatever else is pending (planned 2026-10-24 with a placeholder ARN
   of the same length):
   - six `has moved to module.honua.…redis…[0]` lines (random password,
     security group, subnet group, replication group, secret, secret version)
     and **no** create/destroy of any Redis object;
   - `aws_iam_role_policy.lambda_operation_key_ring[0]` created (GetSecretValue on
     exactly the step-2 ARN);
   - `module.honua.aws_iam_policy.lambda_secrets` updated (adds the
     `redis-connection` secret);
   - `module.honua.aws_security_group.lambda` updated (6379 egress to the VPC CIDR;
     keep it a CIDR rule, see README "CIDR-egress gotcha");
   - `module.honua.aws_lambda_function.this` environment adds
     `ConnectionStrings__redis = aws:secretsmanager:<redis-connection ARN>` and
     `Operations__SecretChannel__KeyRingCertificatePkcs12 = aws:secretsmanager:honua-demo-demo/operation-key-ring`
     (the secret NAME, derived from the step-2 ARN, to save 54 bytes);
   - environment budget **4065 of 4096 bytes** (re-planned 2026-10-24 after the
     `Limits__Connections__*` pool caps were added). It only fits because the
     licensing change removed 215 bytes of `Licensing__*` and the key-ring
     reference uses the name form. Do not add any variable in the same change.

5. **Before apply**: confirm `aws_lambda_alias.live` follows a clean
   (`HONUA_SKIP_MIGRATIONS=true`) version (rc1 runbook §11). Note the current
   `live` version as the rollback target.

6. **Apply** the saved plan, then verify:
   - `/healthz/ready` 200 within a minute;
   - no `KeyRingCertificate` / `SecretChannel` error in
     `aws logs filter-log-events --log-group-name /aws/lambda/honua-demo-demo-honua --filter-pattern '"KeyRing"'`;
   - `runbook/scripts/rc1-upgrade-probe.sh after-redis` passes the `mapserver-export`
     and `imageserver-export` checks (PNG, > 0 bytes);
   - no new `SharedWriteRejectedRedisRequired` events.

7. **Rollback**: repoint `live` to the version noted in step 5
   (`aws lambda update-alias --function-name honua-demo-demo-honua --name live --function-version <n>`);
   published versions freeze their environment, so this drops Redis
   immediately. Then revert the flip in Terraform (the moved blocks must be
   swapped back in the same change) before any further apply. Leave the secret:
   deleting it destroys the only key that can read the Redis key ring.

## Flip diff (ready to apply after step 2)

Generated against this branch; `terraform plan` of this diff with a placeholder
ARN produced the result in step 4.

```diff
diff --git a/stacks/aws/demo.tfvars b/stacks/aws/demo.tfvars
index deb264c..7c5893f 100644
--- a/stacks/aws/demo.tfvars
+++ b/stacks/aws/demo.tfvars
@@ -59,8 +59,9 @@ enable_redis = true
 # Lambda for the 2026-10-22 rc cutover: honua-server#6500 (Lambda + Redis boot aborts
 # without Operations:SecretChannel:KeyRingCertificatePath) and #6487 (Redis-off admin
 # API keys are per process, a known gap). See redis.tf.
-wire_redis_connection = false
-enable_gp_batch       = false
+wire_redis_connection                     = true
+operation_key_ring_certificate_secret_arn = "<ARN printed by step 2 of runbook/exports-redis-prereq.md>"
+enable_gp_batch                           = false
 
 # Pro is adopted BY ARN — the module creates no secret and never reads the licence envelope, so
 # enabling it needs no secret material and no terraform import.
diff --git a/stacks/aws/redis.tf b/stacks/aws/redis.tf
index 46a8198..c2787b4 100644
--- a/stacks/aws/redis.tf
+++ b/stacks/aws/redis.tf
@@ -140,27 +140,30 @@ resource "aws_secretsmanager_secret_version" "redis_connection_unwired" {
   secret_string = "${aws_elasticache_replication_group.redis_unwired[0].primary_endpoint_address}:6379,password=${random_password.redis_auth_unwired[0].result},ssl=true"
 }
 
+# Redis is WIRED (wire_redis_connection = true): the six objects live in the
+# module again (redis_enabled = true). The reverse blocks (module -> *_unwired)
+# are removed in the same change; a moved block cannot point both ways.
 moved {
-  from = module.honua.random_password.redis_auth[0]
-  to   = random_password.redis_auth_unwired[0]
+  from = random_password.redis_auth_unwired[0]
+  to   = module.honua.random_password.redis_auth[0]
 }
 moved {
-  from = module.honua.aws_security_group.redis[0]
-  to   = aws_security_group.redis_unwired[0]
+  from = aws_security_group.redis_unwired[0]
+  to   = module.honua.aws_security_group.redis[0]
 }
 moved {
-  from = module.honua.aws_elasticache_subnet_group.redis[0]
-  to   = aws_elasticache_subnet_group.redis_unwired[0]
+  from = aws_elasticache_subnet_group.redis_unwired[0]
+  to   = module.honua.aws_elasticache_subnet_group.redis[0]
 }
 moved {
-  from = module.honua.aws_elasticache_replication_group.redis[0]
-  to   = aws_elasticache_replication_group.redis_unwired[0]
+  from = aws_elasticache_replication_group.redis_unwired[0]
+  to   = module.honua.aws_elasticache_replication_group.redis[0]
 }
 moved {
-  from = module.honua.aws_secretsmanager_secret.redis_connection[0]
-  to   = aws_secretsmanager_secret.redis_connection_unwired[0]
+  from = aws_secretsmanager_secret.redis_connection_unwired[0]
+  to   = module.honua.aws_secretsmanager_secret.redis_connection[0]
 }
 moved {
-  from = module.honua.aws_secretsmanager_secret_version.redis_connection[0]
-  to   = aws_secretsmanager_secret_version.redis_connection_unwired[0]
+  from = aws_secretsmanager_secret_version.redis_connection_unwired[0]
+  to   = module.honua.aws_secretsmanager_secret_version.redis_connection[0]
 }
```
