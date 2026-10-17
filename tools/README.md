# Reproduce the congestion matrix

Run in this coordinator worktree using the existing SUMO environment. The only branch input is an exported **single committed revision**; nothing is merged. Use a fresh snapshot folder, or verify the saved archive and source hashes before reusing an existing one.

```powershell
Set-Location 'D:\SUMA test-wt\congestion-coordination'
New-Item -ItemType Directory -Force data/work | Out-Null
git -C 'D:\SUMA test-wt\signals' archive --format=tar -o 'D:\SUMA test-wt\congestion-coordination\data\work\signals-0b49c08.tar' 0b49c08 backend/flowstate data/networks/district.net.xml data/networks/district.city.json data/networks/district.stops.add.xml
& 'D:\SUMA test\.venv\Scripts\python.exe' -c "import tarfile; from pathlib import Path; tarfile.open('data/work/signals-0b49c08.tar').extractall('data/work/frozen-signals', filter='data'); Path('data/work/frozen-signals/REVISION').write_text('0b49c08c2650bc96756451a846f0c1c244ce31e9', encoding='utf-8')"
& 'D:\SUMA test\.venv\Scripts\python.exe' tools/run_congestion_matrix.py
& 'D:\SUMA test\.venv\Scripts\python.exe' tools/grade_congestion_matrix.py
& 'D:\SUMA test\.venv\Scripts\python.exe' -m pytest tools/test_congestion_contract.py -q
```

The runner uses two isolated native processes, never the API's live process. Inputs are read from the snapshot; native work files go to this worktree's ignored `data/work/coordination`, and reports/logs to `reports/frozen-matrix`. Check `index.json` for all ten successful process exits before grading. Retain failed runs instead of selecting passing seeds.

`held_peak` holds the seeded commuter rate, vehicle distribution and OD profile at its drawn peak throughout 300 s warm-up + 300 s observation. The nine runs span three requested load settings and seeds 7/42/99. One `recipe_clock` reference uses the unmodified time curve. Both arms execute actual SUMO with collision checks and warnings enabled; the override is a diagnostic input, not a production scenario modification. Current native policies retain max-depart-delay=180 and time-to-teleport=240, so loss/rescue must remain visible.

The graded gates are necessary, not sufficient: exact sample coverage, native contacts/route validity/teleports/discards, initial density and its persistence. Full pre-observation stability, individual stop-and-go histories, integer external-queue conservation and causal receiving-lane/cycle tests are explicitly pending; they are never counted as passing. Queue measurements join connected main/bay lane pieces and use strictly halted vehicles with ≤10 m intervehicle gaps. Internal crossing lengths and pockets limit that geometric proxy; use the native causal detector tests in the acceptance contract for qualification.

Stop-sample gaps add minGap back to native getLeader distance. They repeat vehicles over time and do not yet filter all bicycle leaders/lateral maneuvers; use them as diagnostic distributions, not a certified fleet equilibrium distribution. Native sampling/routing checks add overhead. Concurrent runs and active developers mean these wall times are not a dedicated performance benchmark.
