#!/usr/bin/env python3
import json,sys
from pathlib import Path
s=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected={"DBSnapshotIdentifier":"honua-demo-pre-574c804-ed316a743591","DBInstanceIdentifier":"honua-demo-demo-postgres","DbiResourceId":"db-WNTITZLSHMDLINGEGSB6TEZQYI","Status":"available","SnapshotType":"manual","Engine":"postgres","EngineVersion":"15.17","Encrypted":True,"KmsKeyId":"arn:aws:kms:us-west-2:585192672263:key/4bccd8dc-27dc-4390-9393-bd2dfad9cbc8"}
for key,value in expected.items():
    if s.get(key)!=value: raise RuntimeError(f"snapshot {key} drifted")
if s.get("DBSnapshotArn")!="arn:aws:rds:us-west-2:585192672263:snapshot:honua-demo-pre-574c804-ed316a743591": raise RuntimeError("snapshot ARN drifted")
tags={x.get("Key"):x.get("Value") for x in s.get("TagList",[])}
if tags.get("HonuaMigration")!="574c804" or tags.get("PendingScriptsSha256")!="ed316a74359ebf8a72348159955745ab3032fbfe035c4453dda826c0ce7ac6a3": raise RuntimeError("snapshot tags drifted")
print("db migration recovery snapshot: PASS")
