#!/usr/bin/env python3
"""Rebuild ProgramBench task images natively for arm64 and push them under the user's Docker Hub namespace.

Official images: programbench/<org>_1776_<repo>.<sha>:task_cleanroom_v6 (amd64 only, no Dockerfiles published).
Their BuildKit history holds the whole recipe except the workspace, which is cloned from a private GitHub org;
we copy /workspace out of the official image instead (reconstruct.py). Result: bdqnghi/<same repo name>:<tag>, so
`PROGRAMBENCH_DOCKER_ORG=bdqnghi programbench eval ...` works unchanged.
"""
import argparse, json, os, re, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RECON = ROOT.parent / "tools" / "reconstruct.py"
lock = threading.Lock()


def log(msg):
    with lock:
        print(time.strftime("%H:%M:%S"), msg, flush=True)


def run(cmd, logfile, timeout=None):
    with open(logfile, "ab") as f:
        f.write(f"\n$ {' '.join(cmd)}\n".encode())
        f.flush()
        return subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=timeout).returncode


def pull(ref, logfile):
    for attempt in range(12):
        p = subprocess.run(["docker", "pull", "-q", "--platform", "linux/amd64", ref], capture_output=True, text=True)
        if p.returncode == 0:
            return True
        if "toomanyrequests" in (p.stdout + p.stderr):
            log(f"[hub] rate limited pulling {ref}; waiting 5 min")
            time.sleep(300)
            continue
        with open(logfile, "a") as f:
            f.write(p.stdout + p.stderr)
        return False
    return False


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


def build_instance(inst, args, status):
    repo = inst.replace("__", "_1776_")
    src = f"programbench/{repo}:{args.tag}"
    dst = f"{args.namespace}/{repo}:{args.tag}"
    prev = status.data.get(inst) or {}
    if not args.force and prev.get("ok") and (prev.get("pushed") or not args.push):
        log(f"{inst}: done previously")
        return True
    wd = ROOT / "work" / repo
    logfile = ROOT / "logs" / f"{inst}.log"
    logfile.parent.mkdir(exist_ok=True)
    logfile.write_text("")
    t0 = time.time()
    if not (wd / "Dockerfile").exists() or args.force:
        log(f"{inst}: pulling official image")
        if not pull(src, logfile):
            log(f"{inst}: FAILED (pull)")
            status.set(inst, ok=False, error="pull")
            return False
        os_release = subprocess.run(["docker", "run", "--rm", "--platform", "linux/amd64", "--entrypoint", "cat", src, "/etc/os-release"],
                                    capture_output=True, text=True).stdout
        m = re.search(r'VERSION_ID="?(\d+\.\d+)', os_release)
        base = f"ubuntu:{m.group(1)}" if "Ubuntu" in os_release and m else None
        if not base:
            log(f"{inst}: FAILED (unknown base: {os_release[:80]!r})")
            status.set(inst, ok=False, error="base")
            return False
        rc = run([sys.executable, str(RECON), src, str(wd), "--from", base, "--copy", "/workspace",
                  "--skip", r"github_token|gnever-reveng|GIT_ASKPASS|_reveng_"], logfile)
        subprocess.run(["docker", "rmi", src], capture_output=True)
        if rc != 0:
            log(f"{inst}: FAILED (reconstruct)")
            status.set(inst, ok=False, error="reconstruct")
            return False
    log(f"{inst}: building")
    rc = run(["docker", "build", "--platform", "linux/arm64", "--progress=plain", "-t", dst, str(wd)], logfile, timeout=args.timeout)
    dur = round(time.time() - t0)
    if rc != 0:
        log(f"{inst}: FAILED ({dur}s) see {logfile}")
        status.set(inst, ok=False, seconds=dur, error="build")
        return False
    pushed = True
    if args.push:
        pushed = run(["docker", "push", dst], logfile) == 0
    log(f"{inst}: built ({dur}s){', pushed' if args.push and pushed else (', PUSH FAILED' if args.push else '')}")
    if args.push and pushed and not args.keep:
        subprocess.run(["docker", "rmi", dst], capture_output=True)
    status.set(inst, ok=True, pushed=pushed, seconds=dur)
    return pushed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", action="append")
    ap.add_argument("--namespace", default="bdqnghi")
    ap.add_argument("--tag", default="task_cleanroom_v6")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--timeout", type=int, default=2 * 3600)
    args = ap.parse_args()
    instances = json.load(open(ROOT / "instances.json"))
    if args.instance:
        instances = [i for i in instances if i in set(args.instance)]
    log(f"plan: {len(instances)} instances -> {args.namespace}/<repo>:{args.tag} (push={args.push})")
    status = Status(ROOT / "status.json")
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(build_instance, i, args, status): i for i in instances}
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
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
