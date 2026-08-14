#!/usr/bin/env python3
"""Hostile unit tests for fixed request, journal, rollback, and sanitized output."""
import importlib.util,json,os,sys,tempfile,types,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; SOURCE=ROOT/"stacks/aws-db-migration-runner/runner"; MANIFEST=json.loads((SOURCE/"migration-manifest.v1.json").read_text())
class Connection:
 def __init__(self,journal=None,fail=None): self.journal=journal or [f"Honua.Server.Migrations.{i:03d}_X.sql" for i in range(1,92)]; self.calls=[]; self.fail=fail
 def run(self,sql,**kw):
  self.calls.append(sql)
  if self.fail and self.fail in sql: raise RuntimeError("password=do-not-leak")
  if "to_regclass" in sql:return [["schema_versions"]]
  if "SELECT scriptname" in sql:return [[x] for x in self.journal]
  if "pg_try_advisory" in sql:return [[True]]
  if "INSERT INTO public.schema_versions" in sql:self.journal.append(kw["name"])
  return []
 def close(self): self.calls.append("CLOSE")
class Tests(unittest.TestCase):
 def setUp(self):
  self.c=Connection(); secret=types.SimpleNamespace(get_secret_value=lambda **_: {"SecretString":"Host=db;Database=honua;Username=u;Password=p"})
  sys.modules["boto3"]=types.SimpleNamespace(client=lambda *_a,**_k:secret)
  sys.modules["pg8000"]=types.SimpleNamespace(native=types.SimpleNamespace(Connection=lambda **_:self.c)); sys.modules["pg8000.native"]=sys.modules["pg8000"].native
  spec=importlib.util.spec_from_file_location("runner_handler",SOURCE/"handler.py"); self.m=importlib.util.module_from_spec(spec); spec.loader.exec_module(self.m)
  os.environ.update({"DB_SECRET_ARN":"arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/connection-string-Ab12Cd","EXPECTED_SOURCE_COMMIT":MANIFEST["sourceCommit"],"EXPECTED_CANDIDATE_IMAGE_DIGEST":MANIFEST["candidateImageDigest"],"EXPECTED_PREFLIGHT_SHA256":MANIFEST["preflightReceiptSha256"],"EXPECTED_PENDING_SET_SHA256":MANIFEST["pendingScriptsSha256"],"MIGRATION_OPERATION":"apply-092-105","SOURCE_HANDLER_SHA256":self.m._sha((SOURCE/"handler.py").read_bytes()),"SOURCE_MANIFEST_SHA256":self.m._sha((SOURCE/"migration-manifest.v1.json").read_bytes())})
  self.m.ssl.create_default_context=lambda **_:types.SimpleNamespace(check_hostname=True,verify_mode=None)
 def test_success_is_exact_and_transactional(self):
  out=self.m.handler({"operation":"apply-092-105"},None); self.assertEqual(out["status"],"passed"); self.assertEqual(out["migration"]["appliedScriptCount"],14); self.assertIn("COMMIT",self.c.calls); self.assertNotIn("password",json.dumps(out).lower())
 def test_extra_request_rejected_without_connect(self):
  out=self.m.handler({"operation":"apply-092-105","sql":"DROP"},None); self.assertEqual(out["failure"],"invalid-request"); self.assertEqual(self.c.calls,[])
 def test_replay_rejected_and_rolled_back(self):
  self.c.journal += [x["name"] for x in MANIFEST["scripts"]]; out=self.m.handler({"operation":"apply-092-105"},None); self.assertEqual(out["failure"],"journal-before-boundary-drift"); self.assertIn("ROLLBACK",self.c.calls)
 def test_unknown_journal_rejected(self):
  self.c.journal[10]="unknown"; out=self.m.handler({"operation":"apply-092-105"},None); self.assertEqual(out["failure"],"journal-unknown-script")
 def test_failure_is_sanitized_and_rolls_back(self):
  self.c.fail="ALTER TABLE"; out=self.m.handler({"operation":"apply-092-105"},None); self.assertEqual(out["failure"],"migration-transaction-failed"); self.assertNotIn("password",json.dumps(out).lower()); self.assertIn("ROLLBACK",self.c.calls)
 def test_environment_drift_fails_before_db(self):
  os.environ["EXPECTED_SOURCE_COMMIT"]="0"*40; out=self.m.handler({"operation":"apply-092-105"},None); self.assertEqual(out["failure"],"immutable-environment-drift"); self.assertEqual(self.c.calls,[])
if __name__=="__main__": unittest.main()
