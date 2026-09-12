#!/usr/bin/env python3
"""Build the DeepSWE (datacurve-ai/deep-swe) task images natively for arm64 and push them to Docker Hub.

Each task ships environment/Dockerfile (FROM public.ecr.aws/x8v8d7g8/mars-base:latest, multi-arch), so this is a plain
`docker build` per task. Images are tagged <namespace>:<task_id>, matching the docker_image rewritten into task.toml.
Resumable via status.json; logs in logs/<task>.log.
"""
import argparse, json, os, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TASKS = ROOT / "deep-swe" / "tasks"
lock = threading.Lock()


def log(msg):
    with lock:
        print(time.strftime("%H:%M:%S"), msg, flush=True)


def run(cmd, logfile, timeout=None):
    with open(logfile, "ab") as f:
        f.write(f"\n$ {' '.join(cmd)}\n".encode())
        f.flush()
        return subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=timeout).returncode


class Status:
    def __init__(self, path):
        self.path = path
        self.data = json.load(open(path)) if path.exists() else {}

    def set(self, key, **kw):
        with lock:
            self.data[key] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **kw}
            tmp = self.path.with_suffix(".tmp")
            json.dump(self.data, open(tmp, "w"), indent=1)
            os.replace(tmp, self.path)


def build_task(task, args, status):
    ref = f"{args.namespace}:{task}"
    prev = status.data.get(task) or {}
    if not args.force and prev.get("ok") and (prev.get("pushed") or not args.push):
        log(f"{task}: done previously")
        return True
    ctx = TASKS / task / "environment"
    logfile = ROOT / "logs" / f"{task}.log"
    logfile.parent.mkdir(exist_ok=True)
    logfile.write_text("")
    t0 = time.time()
    log(f"{task}: building")
    rc = run(["docker", "build", "--platform", "linux/arm64", "--progress=plain", "-t", ref, str(ctx)],
             logfile, timeout=args.timeout)
    dur = round(time.time() - t0)
    if rc != 0:
        log(f"{task}: FAILED ({dur}s) see {logfile}")
        status.set(task, ok=False, seconds=dur)
        return False
    pushed = True
    if args.push:
        rc = run(["docker", "push", ref], logfile)
        pushed = rc == 0
    log(f"{task}: built ({dur}s){', pushed' if args.push and pushed else (', PUSH FAILED' if args.push else '')}")
    if args.push and pushed and not args.keep:
        subprocess.run(["docker", "rmi", ref], capture_output=True)
    status.set(task, ok=True, pushed=pushed, seconds=dur)
    return pushed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", action="append", help="only these task ids; repeatable")
    ap.add_argument("--namespace", default="bdqnghi/deepswe")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep images locally after push")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--timeout", type=int, default=2 * 3600)
    args = ap.parse_args()
    tasks = sorted(p.name for p in TASKS.iterdir() if (p / "environment" / "Dockerfile").exists())
    if args.task:
        tasks = [t for t in tasks if t in set(args.task)]
    log(f"plan: {len(tasks)} tasks -> {args.namespace} (push={args.push})")
    status = Status(ROOT / "status.json")
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(build_task, t, args, status): t for t in tasks}
        for f in as_completed(futs):
            try:
                good = f.result()
            except Exception as e:
                log(f"{futs[f]}: EXCEPTION {e}")
                status.set(futs[f], ok=False, error=str(e))
                good = False
            ok += good
            fail += not good
    log(f"DONE ok={ok} fail={fail}")
    for t, v in status.data.items():
        if not v.get("ok"):
            print("  failed:", t)
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
