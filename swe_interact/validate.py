#!/usr/bin/env python3
"""Validate a SWE-Interact arm64 image with the task's reference solution and its own verifier, without an LLM.

Mirrors what Harbor's oracle agent + shared-mode verifier do for the single-turn variant of the task (the
single-turn and multi-turn variants ship byte-identical tests/ and solution/ directories, and the multi-turn
final step's canonical_test.sh is tests/test.sh plus a diff-metrics block):
  docker run <image> sleep infinity; upload solution/ -> /solution and tests/ -> /tests;
  bash /solution/solve.sh; bash /tests/test.sh; read /logs/verifier/reward.txt.

Pass criteria:
  swebenchpro, deepswe: reward == 1.
  rf: the verifier's deterministic half, tests_reward == 1.0 (full test suite at base vs base+gold+test patch,
      no pass->fail / missing->fail among the task's relevant tests). The LLM rubric half (evaluate_rubrics.py,
      EVAL_MODEL) is not run: no API key is passed, so reward.txt is 0 by construction and is recorded only.
  --control additionally runs the verifier on the untouched repository (expects reward 0 / for rf, just records).

Usage: validate.py <task> [--image REF] [--control]
"""
import argparse, json, os, re, subprocess, sys, time, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# a pristine export of upstream b32f98c (`git archive b32f98c | tar -x -C work/upstream`): the shared checkout may
# have swe-interact-arm64.patch applied, and the rules here rewrite the upstream files
SRC = Path(os.environ.get("SWE_INTERACT_SRC", str(Path(__file__).resolve().parent / "work" / "upstream")))
TASKS = SRC / "data" / "multiturn"


def _run(cmd, logfile, timeout=None, capture=False):
    with open(logfile, "ab") as f:
        f.write(f"\n$ {' '.join(map(str, cmd))}\n".encode())
        f.flush()
        try:
            if capture:
                r = subprocess.run(cmd, capture_output=True, timeout=timeout)
                f.write(r.stdout + r.stderr)
                return r.returncode, r.stdout.decode(errors="replace")
            return subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=timeout).returncode, ""
        except subprocess.TimeoutExpired:
            f.write(b"\n!! TIMEOUT\n")
            return 124, ""


def verifier_timeout(task):
    toml = (SRC / "data" / "singleturn" / task / "task.toml").read_text()
    m = re.search(r"^\[verifier\][^\[]*?timeout_sec\s*=\s*([\d.]+)", toml, re.M | re.S)
    return int(float(m.group(1))) if m else 3600


def run_once(task, image, logfile, apply_solution=True, keep=False, platform="linux/arm64"):
    fam = task.split("_", 1)[0]
    name = f"swi-val-{uuid.uuid4().hex[:10]}"
    tdir = TASKS / task
    outdir = ROOT / "work" / task / (("validation" if apply_solution else "control") + ("" if platform == "linux/arm64" else "-" + platform.split("/")[1]))
    outdir.mkdir(parents=True, exist_ok=True)
    res = {"solution": apply_solution}
    try:
        rc, _ = _run(["docker", "run", "-d", "--platform", platform, "--name", name, "--entrypoint", "",
                      image, "sleep", "infinity"], logfile)
        if rc:
            return {**res, "ok": False, "reason": "container did not start"}
        _run(["docker", "exec", name, "mkdir", "-p", "/logs/verifier", "/logs/agent", "/logs/artifacts"], logfile)
        _run(["docker", "cp", str(tdir / "tests") + "/.", f"{name}:/tests"], logfile)
        if apply_solution:
            _run(["docker", "cp", str(tdir / "solution") + "/.", f"{name}:/solution"], logfile)
            t0 = time.time()
            rc, _ = _run(["docker", "exec", "-e", "DEBIAN_FRONTEND=noninteractive", name, "bash", "-c",
                          "bash /solution/solve.sh > /logs/agent/oracle.txt 2>&1; rc=$?; cat /logs/agent/oracle.txt; exit $rc"],
                         logfile, timeout=3600)
            res["solve_rc"], res["solve_s"] = rc, round(time.time() - t0)
        t0 = time.time()
        env = ["-e", "EVAL_MODEL=none"] if fam == "rf" else []
        rc, _ = _run(["docker", "exec", *env, name, "bash", "-c", "bash /tests/test.sh"], logfile,
                     timeout=verifier_timeout(task) + 600)
        res["test_rc"], res["test_s"] = rc, round(time.time() - t0)
        _run(["docker", "cp", f"{name}:/logs/verifier/.", str(outdir)], logfile)
        reward_f = outdir / "reward.txt"
        reward = reward_f.read_text().strip() if reward_f.exists() else None
        res["reward"] = reward
        if fam == "rf":
            tr = outdir / "tests_reward.txt"
            res["tests_reward"] = tr.read_text().strip() if tr.exists() else None
            cmp_f = outdir / "comparison_results.json"
            if cmp_f.exists():
                c = json.load(open(cmp_f))
                res["relevant"] = c.get("relevant_test_count")
                res["full_p2f"], res["full_m2f"] = c.get("full_p2f_count"), c.get("full_m2f_count")
                res["filtered_summary"] = c.get("filtered_summary")
                if "error" in c:
                    res["comparison_error"] = c["error"]
            ok = res["tests_reward"] is not None and float(res["tests_reward"]) >= 1.0
            # guard against a vacuous pass: the relevant tests must have run and passed after the mutation
            fs = res.get("filtered_summary") or {}
            passed_after = sum(fs.get(k, 0) for k in ("pass_to_pass", "fail_to_pass", "missing_to_pass"))
            res["relevant_passed_after"] = passed_after
            if ok and passed_after == 0:
                ok = False
                res["reason"] = "tests_reward=1 but no relevant test passed after the mutation (vacuous)"
        else:
            ok = reward is not None and float(reward) >= 1.0
        res["ok"] = ok if apply_solution else (reward is not None and float(reward) == 0.0)
        # a known upstream quirk: accept when the result equals what the official amd64 image gives (under qemu)
        off_f = ROOT / "rules" / task / "official_amd64_validation.json"
        if apply_solution and not res["ok"] and platform == "linux/arm64" and off_f.exists():
            off = json.load(open(off_f))
            keys = ("reward", "tests_reward", "relevant", "full_p2f", "full_m2f", "relevant_passed_after")
            if all(str(res.get(k)) == str(off.get(k)) for k in keys if k in off):
                res["ok"] = True
                res["vacuous_or_failing_as_official"] = res.pop("reason", None)
                res["matches_official_amd64"] = True
                res["upstream_quirk"] = off.get("note")
        if not res["ok"] and "reason" not in res:
            res["reason"] = f"reward={reward}" + (f" tests_reward={res.get('tests_reward')}" if fam == "rf" else "")
        return res
    finally:
        if not keep:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def validate(task, image, logfile, control=False):
    res = run_once(task, image, logfile, apply_solution=True)
    if control:
        res["control"] = run_once(task, image, logfile, apply_solution=False)
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--image")
    ap.add_argument("--control", action="store_true")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--platform", default="linux/arm64", help="linux/amd64 + --image <official> checks upstream under qemu")
    a = ap.parse_args()
    sys.path.insert(0, str(ROOT))
    from build import image_for
    lf = ROOT / "logs" / f"{a.task}.validate.log"
    lf.parent.mkdir(exist_ok=True)
    r = run_once(a.task, a.image or image_for(a.task), lf, keep=a.keep, platform=a.platform)
    if a.control:
        r["control"] = run_once(a.task, a.image or image_for(a.task), lf, apply_solution=False)
    print(json.dumps(r, indent=1))
