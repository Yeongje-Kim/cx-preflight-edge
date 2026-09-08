
import hashlib,json,subprocess,time,urllib.request,urllib.error
from pathlib import Path
from datetime import datetime, timezone
root=Path("/home/radxa/cx-preflight-edge/verification/9385058")
out=root/"npu-results"
data=json.loads((out/"results.json").read_text())
assert data["success"]
protected=("rules.executed.json","rules.approved.json","facts.json","verdicts.jsonl","telemetry.csv")
before={row["session"]:{name:hashlib.sha256((root/"sessions"/row["session"]/name).read_bytes()).hexdigest() for name in protected} for row in data["trials"]}
subprocess.run(["systemctl","--user","restart","cxpe-npu-recheck-9385058"],check=True)
base="http://127.0.0.1:8089/api/v1"
def call(path,body=None):
 req=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),headers={"Content-Type":"application/json"})
 try:
  with urllib.request.urlopen(req,timeout=180) as r:return r.status,json.loads(r.read())
 except urllib.error.HTTPError as e:return e.code,None
for attempt in range(50):
 try:
  code,_=call("/sessions")
  if code==200:break
 except urllib.error.URLError:pass
 time.sleep(.2)
else:raise TimeoutError("server restart failed")
checks=[]
for row in data["trials"]:
 sid=row["session"]
 code,session=call("/sessions/"+sid)
 assert code==200 and session["has_run"] and session["has_report"]
 assert session["meta"]["summary"]["overall"]==row["expected"]
 assert call("/sessions/"+sid+"/run",{"speed":0})[0]==409
 assert call("/sessions/"+sid+"/approve",{"force":True})[0]==409
 assert before[sid]=={name:hashlib.sha256((root/"sessions"/sid/name).read_bytes()).hexdigest() for name in protected}
 checks.append({"case":row["case"],"session":sid,"stored_result":session["meta"]["summary"]["overall"],"rerun_http":409,"force_reapproval_http":409,"history_unchanged":True})
sid=data["trials"][0]["session"]
code,regen=call("/sessions/"+sid+"/report",{})
assert code==200
assert before[sid]=={name:hashlib.sha256((root/"sessions"/sid/name).read_bytes()).hexdigest() for name in protected}
data["server_restart"]={"checks":checks,"report_regeneration_kept_facts":True,"report_source":regen["source"]}
data["finished_utc"]=datetime.now(timezone.utc).isoformat()
(out/"results.json").write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(data["server_restart"],ensure_ascii=False))
