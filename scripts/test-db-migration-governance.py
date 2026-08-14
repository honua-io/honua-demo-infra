#!/usr/bin/env python3
import copy,json,shutil,subprocess,tempfile,types,unittest
import importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def fake_state(module,environment,security_group_id="sg-0123456789abcdef0"):
 version="1"; qualified=module.RUNNER_ARN+":"+version
 def resource(mode,kind,name,attributes): return {"mode":mode,"type":kind,"name":name,"instances":[{"attributes":attributes}]}
 return {"lineage":"lineage","serial":1,"outputs":{"db_migration_runner_qualified_arn":{"value":qualified},"db_migration_runner_version":{"value":version}},"resources":[
  resource("data","aws_iam_policy_document","assume",{"id":"assume"}),
  resource("data","aws_secretsmanager_secret","db_connection",{"name":"honua-demo-demo/connection-string","arn":environment["DB_SECRET_ARN"]}),
  resource("managed","aws_cloudwatch_log_group","runner",{"id":"log"}),
  resource("managed","aws_iam_role","runner",{"name":module.RUNNER_NAME+"-role","arn":module.RUNNER_ROLE}),
  resource("managed","aws_iam_role_policy","runner",{"id":"policy"}),
  resource("managed","aws_security_group","runner",{"id":security_group_id,"vpc_id":module.VPC_ID}),
  resource("managed","aws_lambda_function","runner",{"qualified_arn":qualified,"version":version,"role":module.RUNNER_ROLE,"runtime":"python3.13","handler":"handler.handler","memory_size":512,"source_code_hash":module.ARCHIVE_BASE64_SHA256,"vpc_config":[{"vpc_id":module.VPC_ID,"subnet_ids":list(module.SUBNETS),"security_group_ids":[security_group_id]}],"environment":[{"variables":environment}]})]}
class Tests(unittest.TestCase):
 def test_source_contract(self): subprocess.run(["python",str(ROOT/"scripts/assert-db-migration-runner-source.py")],check=True)
 def test_snapshot_hostile_mutations(self):
  base={"DBSnapshotIdentifier":"honua-demo-pre-092-105-e0ee6b49e116","DBSnapshotArn":"arn:aws:rds:us-west-2:585192672263:snapshot:honua-demo-pre-092-105-e0ee6b49e116","DBInstanceIdentifier":"honua-demo-demo-postgres","DbiResourceId":"db-WNTITZLSHMDLINGEGSB6TEZQYI","Status":"available","SnapshotType":"manual","Engine":"postgres","EngineVersion":"15.17","Encrypted":True,"KmsKeyId":"arn:aws:kms:us-west-2:585192672263:key/4bccd8dc-27dc-4390-9393-bd2dfad9cbc8","TagList":[{"Key":"HonuaMigration","Value":"092-105"},{"Key":"PendingScriptsSha256","Value":"e0ee6b49e11639e971a58efd942f377de588b81bd6f8ed7eb0dae4ccb1a28cb7"}]}
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/"s.json"
   p.write_text(json.dumps(base)); subprocess.run(["python",str(ROOT/"scripts/assert-db-migration-snapshot.py"),str(p)],check=True)
   for key,value in (("Status","creating"),("SnapshotType","automated"),("DBInstanceIdentifier","other"),("DbiResourceId","other"),("EngineVersion","15.16"),("Encrypted",False),("KmsKeyId","other")):
    bad=dict(base); bad[key]=value; p.write_text(json.dumps(bad)); self.assertNotEqual(subprocess.run(["python",str(ROOT/"scripts/assert-db-migration-snapshot.py"),str(p)]).returncode,0)
 def test_operator_single_mutation_order(self):
  text=(ROOT/"scripts/db-migration-runner-invoke.sh").read_text()
  self.assertEqual(text.count("aws rds create-db-snapshot"),1); self.assertEqual(text.count("aws lambda invoke"),1)
  self.assertEqual(text.count("| python scripts/assert-db-migration-runtime.py sanitize"),4)
  self.assertIn('sanitize runner --state "$STATE"',text)
  self.assertNotIn('get-function --function-name "$QUALIFIED_ARN" >',text)
  self.assertLess(text.index("create-db-snapshot"),text.index("db-snapshot-available")); self.assertLess(text.index("assert-db-migration-snapshot.py"),text.index("aws lambda invoke"))
  for forbidden in ("delete-db-snapshot","restore-db-instance","update-function-configuration","publish-version","update-alias","get-secret-value"):
   self.assertNotIn(forbidden,text)
 def test_plan_builds_twice_before_plan_and_apply(self):
  text=(ROOT/"scripts/db-migration-runner-plan-apply.sh").read_text()
  self.assertEqual(text.count('runner/build.py" --source'),2)
  self.assertLess(text.index("runner-a.zip"),text.index('terraform -chdir="$STACK" plan'))
  self.assertLess(text.index('cmp "$EVIDENCE_DIR/runner-a.zip" "$EVIDENCE_DIR/runner-b.zip"'),text.index('terraform -chdir="$STACK" plan'))
  self.assertLess(text.index('python scripts/assert-db-migration-runner-plan.py'),text.index('terraform -chdir="$STACK" apply'))
 def test_result_receipt_rejects_hostile_drift(self):
  spec=importlib.util.spec_from_file_location("migration_result",ROOT/"scripts/assert-db-migration-result.py"); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
  manifest=json.loads((ROOT/"stacks/aws-db-migration-runner/runner/migration-manifest.v1.json").read_text())
  runtime={"qualifiedArn":"arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-db-migration-092-105:1","version":"1","codeSha256":"7HtndesCzzenO9cFPm2ipSS+NDt3heY4S0Ozpwiyx5o="}
  metadata={"StatusCode":200,"ExecutedVersion":"1"}; payload=module.expected_payload()
  snapshot={"DBSnapshotIdentifier":"honua-demo-pre-092-105-e0ee6b49e116","DBSnapshotArn":"arn:aws:rds:us-west-2:585192672263:snapshot:honua-demo-pre-092-105-e0ee6b49e116","DBInstanceIdentifier":"honua-demo-demo-postgres","DbiResourceId":"db-WNTITZLSHMDLINGEGSB6TEZQYI","Status":"available","SnapshotType":"manual","Engine":"postgres","EngineVersion":"15.17","Encrypted":True,"KmsKeyId":"arn:aws:kms:us-west-2:585192672263:key/4bccd8dc-27dc-4390-9393-bd2dfad9cbc8"}
  with tempfile.TemporaryDirectory() as d:
   paths={name:Path(d)/f"{name}.json" for name in ("metadata","payload","snapshot","runtime")}
   def write(values):
    for name,value in values.items(): paths[name].write_text(json.dumps(value))
   write({"metadata":metadata,"payload":payload,"snapshot":snapshot,"runtime":runtime}); module.build(paths["metadata"],paths["payload"],paths["snapshot"],paths["runtime"])
   mutations=[("metadata",{"StatusCode":200,"ExecutedVersion":"2"}),("metadata",{"StatusCode":200,"ExecutedVersion":"1","FunctionError":"Unhandled"}),("snapshot",{**snapshot,"Status":"creating"}),("snapshot",{**snapshot,"DBInstanceIdentifier":"other"})]
   reordered=json.loads(json.dumps(payload)); reordered["migration"]["appliedScripts"]=list(reversed(reordered["migration"]["appliedScripts"])); mutations.append(("payload",reordered))
   for name,bad in mutations:
    values={"metadata":metadata,"payload":payload,"snapshot":snapshot,"runtime":runtime}; values[name]=bad; write(values)
   with self.assertRaises(RuntimeError): module.build(paths["metadata"],paths["payload"],paths["snapshot"],paths["runtime"])
 def test_runtime_sanitizer_and_receipt_reject_drift_without_leaking_raw_configuration(self):
  spec=importlib.util.spec_from_file_location("migration_runtime",ROOT/"scripts/assert-db-migration-runtime.py"); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
  env={"DB_SECRET_ARN":"arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/connection-string-Ab12Cd","EXPECTED_CANDIDATE_IMAGE_DIGEST":module.IMAGE_DIGEST,"EXPECTED_PENDING_SET_SHA256":"e0ee6b49e11639e971a58efd942f377de588b81bd6f8ed7eb0dae4ccb1a28cb7","EXPECTED_PREFLIGHT_SHA256":"357424246af64a7e435ac5e694f50d8935fd61744ac7ce954efb05223dc3c0ee","EXPECTED_SOURCE_COMMIT":"7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad","MIGRATION_OPERATION":"apply-092-105","SOURCE_HANDLER_SHA256":module.hashlib.sha256((module.RUNNER/"handler.py").read_bytes()).hexdigest(),"SOURCE_MANIFEST_SHA256":module.hashlib.sha256((module.RUNNER/"migration-manifest.v1.json").read_bytes()).hexdigest()}
  state=fake_state(module,env)
  runner={"Configuration":{"FunctionName":module.RUNNER_NAME,"FunctionArn":module.RUNNER_ARN+":1","Version":"1","RevisionId":"11111111-1111-1111-1111-111111111111","CodeSha256":module.ARCHIVE_BASE64_SHA256,"Runtime":"python3.13","Handler":"handler.handler","Role":module.RUNNER_ROLE,"MemorySize":512,"Timeout":900,"Architectures":["arm64"],"VpcConfig":{"VpcId":module.VPC_ID,"SubnetIds":list(module.SUBNETS),"SecurityGroupIds":["sg-0123456789abcdef0"]},"Environment":{"Variables":env}},"Concurrency":{"ReservedConcurrentExecutions":1},"Code":{"Location":"https://signed.example/do-not-persist"}}
  candidate={"Configuration":{"FunctionName":"honua-demo-demo-honua","FunctionArn":"arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-honua:40","Version":"40","RevisionId":"0326e209-4231-4acd-9bb4-d3cb89402db0","Environment":{"Variables":{"HONUA_SKIP_MIGRATIONS":"true","PASSWORD":"secret-sentinel"}}},"Code":{"ResolvedImageUri":"repo@"+module.IMAGE_DIGEST,"Location":"https://signed.example/do-not-persist"}}
  preflight={"Configuration":{"FunctionName":"honua-demo-demo-candidate-preflight","FunctionArn":"arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-candidate-preflight:3","Version":"3","RevisionId":"8114746a-223f-4358-a260-bd5699d7f992","CodeSha256":"A"*43+"=","Environment":{"Variables":{"PASSWORD":"secret-sentinel"}}},"Code":{"Location":"https://signed.example/do-not-persist"}}
  live={"AliasArn":"arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-honua:live","Name":"live","FunctionVersion":"39","RevisionId":"4f73dd76-0294-44d3-8362-c6f8606f034e","RoutingConfig":{}}
  sanitized={"runner":module.sanitize("runner",runner,state),"candidate":module.sanitize("candidate",candidate),"preflight":module.sanitize("preflight",preflight),"live":module.sanitize("live",live)}
  evidence=json.dumps(sanitized,sort_keys=True)
  for forbidden in ("secret-sentinel","do-not-persist","Location","Variables"):
   self.assertNotIn(forbidden,evidence)
  mutations=[]
  for path,value in (("CodeSha256","A"*43+"="),("Runtime","python3.12"),("Handler","other.handler"),("Role","arn:aws:iam::585192672263:role/other"),("MemorySize",1024)):
   bad=copy.deepcopy(runner); bad["Configuration"][path]=value; mutations.append(bad)
  for path,value in (("VpcId","vpc-other"),("SubnetIds",module.SUBNETS[:-1]),("SecurityGroupIds",["sg-0123456789abcdef0","sg-11111111111111111"])):
   bad=copy.deepcopy(runner); bad["Configuration"]["VpcConfig"][path]=value; mutations.append(bad)
  bad=copy.deepcopy(runner); bad["Configuration"]["Environment"]["Variables"]["EXPECTED_SOURCE_COMMIT"]="0"*40; mutations.append(bad)
  for bad in mutations:
   with self.assertRaises(RuntimeError): module.sanitize("runner",bad,state)
  for concurrency in (None,{}, {"ReservedConcurrentExecutions":0},{"ReservedConcurrentExecutions":2},{"ReservedConcurrentExecutions":1,"Unexpected":True}):
   bad=copy.deepcopy(runner)
   if concurrency is None: bad.pop("Concurrency")
   else: bad["Concurrency"]=concurrency
   with self.assertRaises(RuntimeError): module.sanitize("runner",bad,state)
  wrong_shape=copy.deepcopy(runner); wrong_shape.pop("Concurrency"); wrong_shape["Configuration"]["ReservedConcurrentExecutions"]=1
  with self.assertRaises(RuntimeError): module.sanitize("runner",wrong_shape,state)
  alternate_sg=copy.deepcopy(runner); alternate_sg["Configuration"]["VpcConfig"]["SecurityGroupIds"]=["sg-11111111111111111"]
  with self.assertRaises(RuntimeError): module.sanitize("runner",alternate_sg,state)
  alternate_secret=copy.deepcopy(runner); alternate_secret["Configuration"]["Environment"]["Variables"]["DB_SECRET_ARN"]="arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/connection-string-Zz99Yy"
  with self.assertRaises(RuntimeError): module.sanitize("runner",alternate_secret,state)
  db={"DBInstanceIdentifier":"honua-demo-demo-postgres","DBInstanceArn":"arn:aws:rds:us-west-2:585192672263:db:honua-demo-demo-postgres","DbiResourceId":"db-WNTITZLSHMDLINGEGSB6TEZQYI","DBInstanceStatus":"available","Engine":"postgres","EngineVersion":"15.17","StorageEncrypted":True,"KmsKeyId":"arn:aws:kms:us-west-2:585192672263:key/4bccd8dc-27dc-4390-9393-bd2dfad9cbc8"}
  with tempfile.TemporaryDirectory() as d:
   paths={name:Path(d)/f"{name}.json" for name in ("runner","candidate","preflight","live","db","state","receipt")}
   for name,value in {**sanitized,"db":db,"state":state}.items(): paths[name].write_text(json.dumps(value))
   args=types.SimpleNamespace(**paths); receipt=module.build(args); paths["receipt"].write_text(json.dumps(receipt))
   module.verify(receipt,module.build(args))
   hostile=dict(receipt); hostile["runnerSha256"]="0"*64
   with self.assertRaises(RuntimeError): module.verify(hostile,module.build(args))
 def test_builder_rejects_cr_in_every_repository_input_class(self):
  spec=importlib.util.spec_from_file_location("migration_build",ROOT/"stacks/aws-db-migration-runner/runner/build.py"); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
  source=ROOT/"stacks/aws-db-migration-runner/runner"; module.validate_repo_inputs(source)
  with tempfile.TemporaryDirectory() as d:
   copy_root=Path(d)/"runner"; shutil.copytree(source,copy_root)
   candidates=[copy_root/"requirements.lock.json",copy_root/"handler.py",copy_root/"migration-manifest.v1.json",copy_root/"migrations/092_CreateSavedMapOperationLog.sql"]
   for path in candidates:
    original=path.read_bytes(); path.write_bytes(original+b"\r")
    with self.assertRaises(RuntimeError): module.validate_repo_inputs(copy_root)
    path.write_bytes(original)
if __name__=="__main__": unittest.main()
