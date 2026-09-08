import sys, json, time
from pathlib import Path
from datetime import datetime, timezone
root=Path('/home/radxa/cx-preflight-edge/verification/d8291a6')
sys.path.insert(0, str(root))
from cxpe.llm.client import GenieXBackend
from cxpe.llm.prompts import report_messages
from cxpe.llm.report import guard_action
from cxpe.llm.extract import parse_json_loose
client=GenieXBackend()
assert client.alive()
output={'purpose':'Separate diagnostic requests after the final lifecycle run; not the original rejected responses.', 'started_utc':datetime.now(timezone.utc).isoformat(), 'cases':[]}
for case in ['pass','fail_start','fail_dropout']:
    facts=json.loads((root/'npu-results'/case/'facts.json').read_text())
    raw=client.complete(report_messages(facts), max_tokens=600,json_mode=True)
    data=parse_json_loose(raw)
    actions=data.get('actions_ko')
    checks=[{'text':a,'accepted':guard_action(a,facts)} for a in actions] if isinstance(actions,list) and all(isinstance(a,str) for a in actions) else []
    row={'case':case,'raw':raw,'checks':checks}
    output['cases'].append(row)
    print(json.dumps(row,ensure_ascii=False), flush=True)
output['finished_utc']=datetime.now(timezone.utc).isoformat()
(root/'npu-results'/'report-diagnostic.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
