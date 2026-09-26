#!/usr/bin/env python3
"""Drive the rf_* family end to end: arm64 base (build_rf_base.py) -> push base -> task image (build.py:
build, validate, push) -> remove local base/task/official images (only after their remote copies are verified).

Base status lives in rf_base_status.json; task status in status.json (same as the other families).
Usage: run_rf.py [--task rf_task-... ...] [--jobs 2]   (default: every rf_* task, in the given order)
"""
import argparse, fcntl, json, os, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import build as B  # noqa: E402

BSTAT = ROOT / "rf_base_status.json"
lock = threading.Lock()


def bstat_update(name, **kw):
    with lock, open(BSTAT.with_suffix(".lock"), "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        d = json.load(open(BSTAT)) if BSTAT.exists() else {}
        d.setdefault(name, {}).update(kw, ts=time.strftime("%Y-%m-%dT%H:%M:%S"))
        tmp = BSTAT.with_suffix(f".tmp{os.getpid()}")
        json.dump(dict(sorted(d.items())), open(tmp, "w"), indent=1)
        os.replace(tmp, BSTAT)


def bstat(name):
    return (json.load(open(BSTAT)) if BSTAT.exists() else {}).get(name, {})


def one(task, args):
    name = B.rf_base_name(task)
    ref = f"{B.BASE_NS}:{name}"
    st = B.Status(ROOT / "status.json").get(task)
    if st.get("pushed") and st.get("validated") and not args.force:
        B.log(f"{task}: done previously")
        return True
    if not bstat(name).get("pushed") or args.force_base:
        B.wait_for_disk(task, 110)
        B.log(f"{task}: building base {ref}")
        t0 = time.time()
        r = subprocess.run([sys.executable, "-u", str(HERE / "build_rf_base.py"), task, "--push", "--keep-official"],
                           capture_output=True, text=True)
        (ROOT / "logs" / f"rfbase_run_{name}.log").write_text(r.stdout + r.stderr)
        if r.returncode:
            bstat_update(name, built=False, pushed=False, error=(r.stderr or r.stdout)[-400:])
            B.log(f"{task}: BASE FAILED: {(r.stderr or r.stdout).strip()[-300:]}")
            return False
        rd = json.loads(subprocess.run(["docker", "image", "inspect", ref, "--format", "{{json .RepoDigests}}"],
                                       capture_output=True, text=True).stdout or "[]")
        dig = next((d.split("@")[1] for d in rd if d.startswith(B.BASE_NS + "@")), None)
        bstat_update(name, built=True, pushed=bool(dig), digest=dig,
                     seconds=round(time.time() - t0), error=None)
        B.log(f"{task}: base pushed")
    r = subprocess.run([sys.executable, "-u", str(ROOT / "build.py"), "--task", task, "--jobs", "1"]
                       + (["--force"] if args.force else []), capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    ok = r.returncode == 0
    if ok:
        B.remove_local_image(ref, f"rf base of {task}, task image pushed")
        subprocess.run(["docker", "rmi", B.official_from(task)], capture_output=True)  # official amd64 (ghcr, not ours)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", action="append")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--force-base", action="store_true")
    args = ap.parse_args()
    tasks = args.task or sorted(p.name for p in B.TASKS.iterdir() if p.name.startswith("rf_"))
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(one, t, args): t for t in tasks}
        for f in as_completed(futs):
            try:
                good = f.result()
            except Exception as e:
                B.log(f"{futs[f]}: EXCEPTION {e!r}")
                good = False
            ok += good
            fail += not good
    B.log(f"RF DONE ok={ok} fail={fail}")


if __name__ == "__main__":
    main()
