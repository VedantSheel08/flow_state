# PPO runs: exact configurations and outcomes

All runs: PPO (Stable-Baselines3 2.9.0), CPU, 6 SUMO workers, training demand seeds 1000-1999 with random intensity 0.7-1.2, six scenario
types excluding `rush_ramp` (never trained on). Commands are run from the repository root with `PYTHONPATH=backend`.
Held-out test seeds (3000+) were used for the final evaluation only.

| run | command (flags that differ from the defaults) | outcome |
|---|---|---|
| run1 | exact command not recorded (launched in an earlier session that was lost); `progress.csv` shows 540 steps per iteration = 6 envs x 90 steps, i.e. the defaults of that time with the joint MLP and v1 observation, validation every 30,000 steps | interrupted by a machine restart at about 150k steps; no `final.zip` / `training_run.json`, target total unknown; best selection wait 89.4 s at 60k; worse than both baselines |
| run2 | `train --seed 2 --steps 300000 --validate-every 30000 --norm-reward --gamma 0.97 --n-steps 180 --batch-size 270 --ent-coef 0.005 --out artifacts/rl/run2` | stopped by hand at 90k steps (selection wait 107.7, 100.0, 117.2 s: not improving); log only |
| run3 | `train --seed 3 --steps 300000 --validate-every 30000 --shared --norm-reward --gamma 0.97 --n-steps 180 --batch-size 270 --ent-coef 0.005 --out artifacts/rl/run3` | completed, 300,240 steps; best selection checkpoint step 150,000 (77.4 s on seeds 2000-2001); **rejected by the gate** |
| run4 | `train --seed 4 --steps 1000000 --validate-every 100000 --val-seeds 4 --agent --blend 0.5 --norm-reward --gamma 0.97 --n-steps 180 --batch-size 540 --ent-coef 0.005 --out artifacts/rl/run4` | completed, 1,002,240 agent samples; best selection checkpoint step 400,008 (84.5 s on seeds 2000-2003); **rejected by the gate** |

run3 and run4 have a `training_run.json` with the full resolved configuration, hardware and library versions. run1 was made with an earlier version of
`train.py` / `cli.py` (before `--norm-reward` existed), so its settings are inferred from its logs as described above. run2 is the command I launched in
this session, not a stored record. run4 was trained with the code in this commit. run3 was trained before `--agent`, observation v2 and the gate-seed split were added; its (non-agent) code path is unchanged by those additive edits, but it was not re-run from this exact commit.

Seed pools: training 1000-1999; checkpoint selection 2000-2003 (run3 used 2000-2001); publication gate 2004-2015; held-out test 3000-3009.

`artifacts/rl/rejected/run3/` contains a copy of the run3 checkpoint and the validation record that an earlier version of `validate-policy`
wrote into the publication path although the gate had failed. It was never offered by the API. The bug is fixed (publication only on a pass,
with a SHA-256 check) and covered by `tests/test_rl_gate.py`.
