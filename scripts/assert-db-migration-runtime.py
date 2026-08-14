#!/usr/bin/env python3
import argparse,hashlib,json,re
from pathlib import Path
def load(p): return json.loads(p.read_text(encoding="utf-8"))
def req(v,m):
    if not v: raise RuntimeError(m)
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def build(a):
    runner,candidate,live,preflight,db,state=map(load,(a.runner,a.candidate,a.live,a.preflight,a.db,a.state))
    rc=runner["Configuration"]; cc=candidate["Configuration"]
    req(rc["FunctionName"]=="honua-demo-demo-db-migration-092-105" and rc["Version"]!="$LATEST","runner is not qualified")
    req(rc["ReservedConcurrentExecutions"]==1 and rc["Timeout"]==900 and rc["Architectures"]==["arm64"],"runner bounds drifted")
    req(cc["Version"]=="40" and cc["RevisionId"]=="0326e209-4231-4acd-9bb4-d3cb89402db0","candidate :40 drifted")
    req(candidate["Code"]["ResolvedImageUri"].endswith("@sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861"),"candidate digest drifted")
    req(live["Name"]=="live" and live["FunctionVersion"]=="39" and live["RevisionId"]=="4f73dd76-0294-44d3-8362-c6f8606f034e" and not live.get("RoutingConfig"),"live alias drifted")
    req(preflight["Configuration"]["Version"]=="3" and preflight["Configuration"]["RevisionId"]=="8114746a-223f-4358-a260-bd5699d7f992","preflight helper drifted")
    req(db["DBInstanceIdentifier"]=="honua-demo-demo-postgres" and db["DbiResourceId"]=="db-WNTITZLSHMDLINGEGSB6TEZQYI" and db["DBInstanceStatus"]=="available","DB identity/status drifted")
    req(db["Engine"]=="postgres" and db["EngineVersion"]=="15.17" and db["StorageEncrypted"] is True,"DB engine/encryption drifted")
    return {"schema":"honua-db-migration-runtime-receipt-v1","qualifiedArn":rc["FunctionArn"],"version":rc["Version"],"revisionId":rc["RevisionId"],"codeSha256":rc["CodeSha256"],"stateLineage":state["lineage"],"stateSerial":state["serial"],"candidateVersion":"40","liveVersion":"39","preflightVersion":"3","candidateSha256":sha(a.candidate),"liveSha256":sha(a.live),"preflightSha256":sha(a.preflight)}
def main():
 p=argparse.ArgumentParser(); p.add_argument("mode",choices=("create","verify"));
 for n in ("runner","candidate","live","preflight","db","state","receipt"): p.add_argument(f"--{n}",type=Path,required=True)
 a=p.parse_args(); actual=build(a)
 if a.mode=="create": a.receipt.write_text(json.dumps(actual,indent=2,sort_keys=True)+"\n",encoding="utf-8")
 else:
  expected=load(a.receipt); req({k:actual[k] for k in actual if not k.endswith("Sha256")}=={k:expected[k] for k in expected if not k.endswith("Sha256")},"runtime identity/state drifted")
 print(f"db migration runtime {a.mode}: PASS")
if __name__=="__main__": main()
