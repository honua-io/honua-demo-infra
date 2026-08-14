#!/usr/bin/env python3
import json,subprocess,tempfile,unittest
import importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
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
  runtime={"qualifiedArn":"arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-db-migration-092-105:1","version":"1"}
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
if __name__=="__main__": unittest.main()
