"""Necessary congestion gates. Missing causal spillback tests never count as passes."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOOR = {'rush':40,'severe':85,'gridlock_request':110}


def evaluate(report):
    rows = [s for s in report['samples'] if 0 <= s['t'] < 300]
    failures = []
    if [r['t'] for r in rows] != list(range(300)):
        failures.append('observation does not contain exactly 300 one-second samples')
    floor = FLOOR[report['case']]
    qualified = sum(r['arterial']['densityVehPerLaneKm'] >= floor for r in rows)
    if qualified < 240:
        failures.append(f'density >= {floor} veh/lane-km for only {qualified}/300 seconds; requires 240')
    if not rows or rows[0]['arterial']['densityVehPerLaneKm'] < floor:
        failures.append('requested density absent at observation start')
    if report['collisionRecords']:
        failures.append(f"{len(report['collisionRecords'])} native collision records")
    if report['invalidRouteIDsSampled']:
        failures.append('invalid active routes observed')
    if report['results']['teleports']:
        failures.append(f"{report['results']['teleports']} automatic teleports")
    if int(report['nativeSummary']['discarded']):
        failures.append(f"{report['nativeSummary']['discarded']} discarded demand; no external holding-queue conservation proof")
    return {'status':'REJECT' if failures else 'NOT YET QUALIFIED', 'failedNecessaryGates':failures,
            'pendingGates':['pre-observation steady-state history', 'vehicle-ID stop-and-go transitions within observation',
                            'causal receiving-lane blockage during legal green', 'integer external-demand conservation'],
            'note':'Adjacent queued-block proxy is not proof of a blocked cycle or actual gridlock.'}


if __name__ == '__main__':
    out=[]
    for path in sorted((ROOT/'reports/frozen-matrix').glob('frozen-*.json')):
        report=json.loads(path.read_text(encoding='utf-8'))
        out.append({'case':report['case'],'seed':report['seed'],'profile':report['profile'],**evaluate(report)})
    (ROOT/'reports/frozen-matrix/acceptance.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'runs':len(out),'rejected':sum(x['status']=='REJECT' for x in out),'accepted':0}))
