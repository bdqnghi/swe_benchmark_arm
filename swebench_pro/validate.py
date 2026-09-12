#!/usr/bin/env python3
"""Run the official SWE-bench Pro eval with GOLD patches against bdqnghi/sweap-images (arm64).
Usage: python3 validate.py [--repo navidrome/navidrome] [--instance ID ...] [--workers N] [--tag LABEL]
A run is successful for an instance when FAIL_TO_PASS and PASS_TO_PASS all pass with the gold patch,
which proves the arm64 image works for that instance.
"""
import argparse, json, subprocess, sys, os
from pathlib import Path
ROOT = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("--repo", action="append")
ap.add_argument("--instance", action="append")
ap.add_argument("--workers", type=int, default=4)
ap.add_argument("--namespace", default="bdqnghi")
ap.add_argument("--platform", default="linux/arm64")
ap.add_argument("--redo", action="store_true")
ap.add_argument("--out", default="validation")
args = ap.parse_args()
rows = json.load(open(ROOT / "instances.json"))
sel = [r["instance_id"] for r in rows
       if (not args.repo or r["repo"] in args.repo) and (not args.instance or r["instance_id"] in args.instance)]
gold = {g["instance_id"]: g for g in json.load(open(ROOT / "gold_patches.json"))}
subset = ROOT / args.out / "gold_subset.json"
subset.parent.mkdir(parents=True, exist_ok=True)
json.dump([gold[i] for i in sel], open(subset, "w"))
print(f"validating {len(sel)} instances with gold patches on {args.namespace}/sweap-images ({args.platform})", flush=True)
cmd = [sys.executable, str(ROOT / "eval_harness/swe_bench_pro_eval.py"),
       "--raw_sample_path", str(ROOT / "swebench_pro_test.jsonl"),
       "--patch_path", str(subset), "--output_dir", str(ROOT / args.out),
       "--scripts_dir", str(ROOT / "eval_harness/run_scripts"),
       "--dockerhub_username", args.namespace, "--use_local_docker",
       "--docker_platform", args.platform, "--num_workers", str(args.workers)]
if args.redo:
    cmd.append("--redo")
rc = subprocess.call(cmd, cwd=str(ROOT / "eval_harness"))
# summarize
res = {}
full = {r["instance_id"]: r for r in rows}
import ast
ok = 0
for i in sel:
    p = ROOT / args.out / i / "gold_output.json"
    res[i] = "NO_OUTPUT"
    if p.exists():
        out = json.load(open(p))
        passed = {t["name"] for t in out.get("tests", []) if t["status"] == "PASSED"}
        need = set()
        line = next(l for l in open(ROOT / "swebench_pro_test.jsonl") if f'"{i}"' in l)
        d = json.loads(line)
        for k in ("fail_to_pass", "pass_to_pass"):
            v = d[k]
            need |= set(ast.literal_eval(v) if isinstance(v, str) else v)
        res[i] = "PASS" if need <= passed else f"FAIL ({len(need - passed)}/{len(need)} missing)"
    ok += res[i] == "PASS"
json.dump(res, open(ROOT / args.out / "summary.json", "w"), indent=1)
print(f"\nRESULT: {ok}/{len(sel)} instances pass with gold patch")
for i, r in res.items():
    if r != "PASS":
        print("  ", r, i)
