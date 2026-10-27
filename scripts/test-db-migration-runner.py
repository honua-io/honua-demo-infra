#!/usr/bin/env python3
"""Hostile unit tests for fixed request, journal, rollback, reader declarations, and sanitized output."""
import importlib.util,json,os,sys,types,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; SOURCE=ROOT/"stacks/aws-db-migration-runner/runner"; MANIFEST_PATH=SOURCE/"migration-manifest.v2.json"; MANIFEST=json.loads(MANIFEST_PATH.read_text())
OP="apply-574c804"; NAMES=[entry["name"] for entry in MANIFEST["scripts"]]
class Connection:
 def __init__(self,journal=None,fail=None,raster=True,reader_table=True):
  self.journal=list(journal) if journal is not None else list(reversed(MANIFEST["executedScripts"])); self.calls=[]; self.fail=fail; self.raster=raster; self.reader_table=reader_table; self.declarations={}
 def run(self,sql,**kw):
  self.calls.append(sql)
  if self.fail and self.fail in sql: raise RuntimeError("password=do-not-leak")
  if "to_regclass('public.schema_versions')" in sql:return [["schema_versions"]]
  if "schema_version_reader_compatibility') IS NOT NULL" in sql:return [[self.reader_table]]
  if "SELECT scriptname" in sql:return [[x] for x in self.journal]
  if "pg_try_advisory" in sql:return [[True]]
  if "postgis_raster" in sql:return [[self.raster]]
  if "INSERT INTO public.schema_versions" in sql:self.journal.append(kw["name"])
  if "INSERT INTO public.schema_version_reader_compatibility" in sql:self.declarations.setdefault(kw["name"],[kw["classification"],kw["sha256"],kw["min_reader_schema"]])
  if "SELECT classification, script_sha256" in sql:return [self.declarations[kw["name"]]] if kw["name"] in self.declarations else []
  return []
 def close(self): self.calls.append("CLOSE")
class Tests(unittest.TestCase):
 def setUp(self):
  self.c=Connection(); secret=types.SimpleNamespace(get_secret_value=lambda **_: {"SecretString":"Host=db;Database=honua;Username=u;Password=p"})
  sys.modules["boto3"]=types.SimpleNamespace(client=lambda *_a,**_k:secret)
  sys.modules["pg8000"]=types.SimpleNamespace(native=types.SimpleNamespace(Connection=lambda **_:self.c)); sys.modules["pg8000.native"]=sys.modules["pg8000"].native
  spec=importlib.util.spec_from_file_location("runner_handler",SOURCE/"handler.py"); self.m=importlib.util.module_from_spec(spec); spec.loader.exec_module(self.m)
  os.environ.update({"DB_SECRET_ARN":"arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/connection-string-Ab12Cd","EXPECTED_SOURCE_COMMIT":MANIFEST["sourceCommit"],"EXPECTED_CANDIDATE_IMAGE_DIGEST":MANIFEST["candidateImageDigest"],"EXPECTED_EXECUTED_SET_SHA256":MANIFEST["executedScriptsSha256"],"EXPECTED_PENDING_SET_SHA256":MANIFEST["pendingScriptsSha256"],"MIGRATION_OPERATION":OP,"SOURCE_HANDLER_SHA256":self.m._sha((SOURCE/"handler.py").read_bytes()),"SOURCE_MANIFEST_SHA256":self.m._sha(MANIFEST_PATH.read_bytes())})
  self.m.ssl.create_default_context=lambda **_:types.SimpleNamespace(check_hostname=True,verify_mode=None)
 def go(self,event=None): return self.m.handler(event if event is not None else {"operation":OP},None)
 def test_success_is_exact_ordered_and_transactional(self):
  out=self.go(); self.assertEqual(out["status"],"passed",out); self.assertEqual(out["migration"]["appliedScripts"],NAMES); self.assertEqual(self.c.journal[-len(NAMES):],NAMES)
  self.assertEqual(self.c.calls[0],"SET search_path TO public"); self.assertIn("COMMIT",self.c.calls); self.assertNotIn("ROLLBACK",self.c.calls); self.assertNotIn("password",json.dumps(out).lower())
  self.assertEqual(sorted(self.c.declarations),sorted(NAMES))
 def test_commands_run_verbatim_in_manifest_order(self):
  self.go(); expected=[]
  for entry in MANIFEST["scripts"]: expected+=json.loads((SOURCE/"migrations"/entry["file"]).read_text())["commands"]
  ran=[c for c in self.c.calls if c in expected]; self.assertEqual(ran,expected)
 def test_journal_order_is_not_part_of_the_baseline_but_membership_is(self):
  self.c.journal=list(MANIFEST["executedScripts"]); self.assertEqual(self.go()["status"],"passed")
 def test_extra_request_rejected_without_connect(self):
  out=self.go({"operation":OP,"sql":"DROP"}); self.assertEqual(out["failure"],"invalid-request"); self.assertEqual(self.c.calls,[])
  out=self.go({"operation":"apply-092-105"}); self.assertEqual(out["failure"],"invalid-request"); self.assertEqual(self.c.calls,[])
 def test_replay_rejected_and_rolled_back(self):
  self.c.journal+=NAMES; out=self.go(); self.assertEqual(out["failure"],"migration-replay-rejected"); self.assertIn("ROLLBACK",self.c.calls)
 def test_baseline_drift_reports_names_and_rolls_back(self):
  baseline=list(MANIFEST["executedScripts"])
  for journal,missing,unexpected in ((baseline[:-1],[baseline[-1]],[]),(baseline+["Honua.Server.Migrations.141_Unexpected.sql"],[],["Honua.Server.Migrations.141_Unexpected.sql"])):
   with self.subTest(size=len(journal)):
    self.c=Connection(journal); out=self.go(); self.assertEqual(out["failure"],"journal-before-boundary-drift"); self.assertEqual(out["detail"],{"missingScripts":missing,"unexpectedScripts":unexpected}); self.assertIn("ROLLBACK",self.c.calls); self.assertFalse(any("INSERT" in c for c in self.c.calls))
 def test_missing_raster_extension_fails_before_any_script(self):
  self.c=Connection(raster=False); out=self.go(); self.assertEqual(out["failure"],"postgis-raster-missing"); self.assertFalse(any("INSERT" in c for c in self.c.calls))
 def test_missing_reader_table_fails_before_any_script(self):
  self.c=Connection(reader_table=False); out=self.go(); self.assertEqual(out["failure"],"reader-compatibility-table-missing"); self.assertFalse(any("INSERT" in c for c in self.c.calls))
 def test_conflicting_existing_declaration_rolls_back(self):
  self.c.declarations[NAMES[0]]=["contract","0"*64,0]; out=self.go(); self.assertEqual(out["failure"],"reader-compatibility-declaration-conflict"); self.assertIn("ROLLBACK",self.c.calls); self.assertNotIn("COMMIT",self.c.calls)
 def test_failure_is_sanitized_and_rolls_back(self):
  first=json.loads((SOURCE/"migrations"/MANIFEST["scripts"][1]["file"]).read_text())["commands"][0]
  self.c.fail=first; out=self.go(); self.assertEqual(out["failure"],"migration-transaction-failed"); self.assertNotIn("password",json.dumps(out).lower()); self.assertIn("ROLLBACK",self.c.calls)
 def test_environment_drift_fails_before_db(self):
  for key in ("EXPECTED_SOURCE_COMMIT","EXPECTED_EXECUTED_SET_SHA256","EXPECTED_PENDING_SET_SHA256","SOURCE_MANIFEST_SHA256"):
   with self.subTest(key=key):
    original=os.environ[key]; os.environ[key]="0"*40; out=self.go(); os.environ[key]=original
    self.assertIn(out["failure"],{"immutable-environment-drift","manifest-source-drift"}); self.assertEqual(self.c.calls,[])
 def test_contract_approval_is_required_only_when_the_manifest_names_one(self):
  self.assertIsNone(MANIFEST["contractApprovalNonce"])
  out=self.go({"operation":OP,"approveContractMigrations":"cdbcc0afc96cae7f"}); self.assertEqual(out["failure"],"invalid-request"); self.assertEqual(self.c.calls,[])
if __name__=="__main__": unittest.main()
