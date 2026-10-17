"""Sequential native runs; branches are read-only inputs, never merged."""
from pathlib import Path
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/frozen-matrix'
OUT.mkdir(parents=True,exist_ok=True)
cases = [('frozen-signals',case,seed,'held_peak') for case in ('rush','severe','gridlock_request') for seed in (7,42,99)]
cases += [('frozen-signals','gridlock_request',42,'recipe_clock')]
results = []
def execute(case_tuple):
    branch,case,seed,profile = case_tuple
    name = f'{branch}-{case}-{seed}-{profile}'
    output = OUT / f'{name}.json'
    command = [sys.executable,str(ROOT/'tools/congestion_audit.py'),'--source',str(ROOT/'data/work'/branch),
               '--case',case,'--seed',str(seed),'--profile',profile,'--output',str(output)]
    with (OUT/f'{name}.log').open('w',encoding='utf-8') as log:
        proc = subprocess.run(command,stdout=log,stderr=log,text=True,encoding='utf-8')
    record = {'branch':branch,'case':case,'seed':seed,'profile':profile,'exitCode':proc.returncode}
    if proc.returncode==0:
        report=json.loads(output.read_text(encoding='utf-8'))
        record.update(report['summary'])
        record['collisions']=len(report['collisionRecords'])
        record['teleports']=report['results']['teleports']
        record['discarded']=int(report['nativeSummary']['discarded'])
        record['demand']=report['demand']
        record['departed']=report['counts']['observation']['departed']
        record['arrived']=report['counts']['observation']['arrived']
        record['vehiclesAtStart']=report['samples'][0]['network']['vehicles']
        record['arterialDensityAtStart']=report['samples'][0]['arterial']['densityVehPerLaneKm']
    else:
        record['errorTail']=(OUT/f'{name}.log').read_text(encoding='utf-8')[-2000:]
    return record

with ThreadPoolExecutor(max_workers=2) as pool:
    for future in as_completed([pool.submit(execute,case) for case in cases]):
        record=future.result()
        results.append(record)
        results.sort(key=lambda x:(x['case'],x['profile'],x['seed']))
        (OUT/'index.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
        print(json.dumps(record),flush=True)
