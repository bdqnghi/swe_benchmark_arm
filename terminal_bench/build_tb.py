#!/usr/bin/env python3
"""Build Terminal-Bench task images natively for arm64 and push them to Docker Hub.

Reads tb-<version>/tasks/<task>/environment (Harbor task format; no prebuilt images exist upstream), builds each
with `docker build`, tags it <namespace>-<version>:<task> (e.g. bdqnghi/terminal-bench-v4:atrx-vep-crispr) and
pushes. Also writes tasks_arm64/<version>/ : a copy of the dataset with `docker_image` set in every task.toml so
Harbor pulls the prebuilt arm64 image instead of building. Tasks with identical environment/ across versions are
built once and tagged for both. Resumable via status.json; logs in logs/<version>/<task>.log.
"""
import argparse, filecmp, json, os, re, shutil, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSIONS = {"v3": "tb-v3.0.0", "v4": "tb-v4.0.0"}
lock = threading.Lock()


def log(msg):
    with lock:
        print(time.strftime("%H:%M:%S"), msg, flush=True)


def run(cmd, logfile, timeout=None):
    with open(logfile, "ab") as f:
        f.write(f"\n$ {' '.join(cmd)}\n".encode())
        f.flush()
        return subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=timeout).returncode


def same_env(a, b):
    cmp = filecmp.dircmp(a, b)
    if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
        return False
    return all(same_env(a / d, b / d) for d in cmp.common_dirs)


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


def has_separate_verifier(task_dir):
    if not (task_dir / "task.toml").is_file():
        return False
    toml = (task_dir / "task.toml").read_text()
    return (task_dir / "tests" / "Dockerfile").exists() and (
        re.search(r'^environment_mode\s*=\s*"separate"', toml, re.M) or re.search(r"^\[verifier\.environment\]", toml, re.M))


def write_dataset_copy(version, tasks, namespace):
    """tasks_arm64/<version>/<task>/... with docker_image pointing at our image."""
    src_root = ROOT / VERSIONS[version] / "tasks"
    dst_root = ROOT / "tasks_arm64" / version
    dst_root.mkdir(parents=True, exist_ok=True)
    for extra in ("dataset.toml", "README.md"):
        if (src_root / extra).exists():
            shutil.copy2(src_root / extra, dst_root / extra)
    for t in tasks:
        dst = dst_root / t
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src_root / t, dst)
        toml = dst / "task.toml"
        text = toml.read_text()
        ref = f"{namespace}-{version}:{t}"
        # strip any docker_image lines (idempotent re-runs), then set ours in [environment]
        text = re.sub(r"^docker_image\s*=.*\n", "", text, flags=re.M)
        if re.search(r"^\[environment\]", text, re.M):
            text = re.sub(r"^\[environment\]\s*$", f'[environment]\ndocker_image = "{ref}"', text, count=1, flags=re.M)
        else:
            text += f'\n[environment]\ndocker_image = "{ref}"\n'
        # Harbor's separate verifier copies the top-level [environment] unless [verifier.environment] is given, so it
        # would run the agent image (no /tests baked) instead of building tests/Dockerfile. Point it at our prebuilt
        # verifier image instead.
        if has_separate_verifier(src_root / t):
            vref = f"{namespace}-{version}:{t}-verifier"
            if re.search(r"^\[verifier\.environment\]", text, re.M):
                text = re.sub(r"^\[verifier\.environment\]\s*$", f'[verifier.environment]\ndocker_image = "{vref}"', text, count=1, flags=re.M)
            else:
                text += f'\n[verifier.environment]\ndocker_image = "{vref}"\n'
        toml.write_text(text)


def build(version, task, aliases, args, status, kind="env"):
    suffix = "-verifier" if kind == "verifier" else ""
    key = f"{version}/{task}{suffix}"
    ref = f"{args.namespace}-{version}:{task}{suffix}"
    prev = status.data.get(key) or {}
    if not args.force and prev.get("ok") and (prev.get("pushed") or not args.push):
        log(f"{key}: done previously")
        return True
    ctx = ROOT / VERSIONS[version] / "tasks" / task / ("tests" if kind == "verifier" else "environment")
    logfile = ROOT / "logs" / version / f"{task}{suffix}.log"
    logfile.parent.mkdir(parents=True, exist_ok=True)
    logfile.write_text("")
    t0 = time.time()
    log(f"{key}: building")
    rc = run(["docker", "build", "--platform", "linux/arm64", "--progress=plain", "-t", ref, str(ctx)],
             logfile, timeout=args.timeout)
    dur = round(time.time() - t0)
    if rc != 0:
        log(f"{key}: FAILED ({dur}s) see {logfile}")
        status.set(key, ok=False, seconds=dur)
        return False
    pushed = True
    refs = [ref] + [f"{args.namespace}-{v}:{task}{suffix}" for v in aliases]
    for alias in refs[1:]:
        subprocess.run(["docker", "tag", ref, alias], check=True)
    if args.push:
        for r in refs:
            pushed = run(["docker", "push", r], logfile) == 0 and pushed
    log(f"{key}: built ({dur}s){' + aliases ' + ','.join(aliases) if aliases else ''}{', pushed' if args.push and pushed else (', PUSH FAILED' if args.push else '')}")
    if args.push and pushed and not args.keep:
        for r in refs:
            subprocess.run(["docker", "rmi", r], capture_output=True)
    status.set(key, ok=True, pushed=pushed, seconds=dur, aliases=aliases)
    for v in aliases:
        status.set(f"{v}/{task}{suffix}", ok=True, pushed=pushed, seconds=0, alias_of=key)
    return pushed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", action="append", choices=list(VERSIONS), help="v3 and/or v4 (default both)")
    ap.add_argument("--task", action="append")
    ap.add_argument("--namespace", default="bdqnghi/terminal-bench")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--timeout", type=int, default=2 * 3600)
    ap.add_argument("--dataset-only", action="store_true", help="only (re)write tasks_arm64/<v>/ pointing at the published images; build nothing")
    args = ap.parse_args()
    versions = args.version or list(VERSIONS)

    # plan: build each distinct environment once; identical v3/v4 environments share an image via alias tags
    jobs = []  # (version, task, aliases)
    seen = {}
    for v in versions:
        tasks = sorted(p.name for p in (ROOT / VERSIONS[v] / "tasks").iterdir() if (p / "environment" / "Dockerfile").exists())
        if args.task:
            tasks = [t for t in tasks if t in set(args.task)]
        for t in tasks:
            dup = None
            for (pv, pt), _ in seen.items():
                if pt == t and same_env(ROOT / VERSIONS[pv] / "tasks" / t / "environment", ROOT / VERSIONS[v] / "tasks" / t / "environment"):
                    dup = (pv, pt)
                    break
            if dup:
                seen[dup].append(v)
            else:
                seen[(v, t)] = []
        write_dataset_copy(v, tasks, args.namespace)
    for (v, t), aliases in seen.items():
        jobs.append((v, t, aliases, "env"))
    seen_v = {}
    for v in versions:
        tasks = sorted(p.name for p in (ROOT / VERSIONS[v] / "tasks").iterdir() if has_separate_verifier(p))
        if args.task:
            tasks = [t for t in tasks if t in set(args.task)]
        for t in tasks:
            dup = None
            for (pv, pt), _ in seen_v.items():
                if pt == t and same_env(ROOT / VERSIONS[pv] / "tasks" / t / "tests", ROOT / VERSIONS[v] / "tasks" / t / "tests"):
                    dup = (pv, pt); break
            if dup:
                seen_v[dup].append(v)
            else:
                seen_v[(v, t)] = []
    for (v, t), aliases in seen_v.items():
        jobs.append((v, t, aliases, "verifier"))
    if args.dataset_only:
        log(f"dataset copies written to {ROOT / 'tasks_arm64'} for {versions}; nothing built")
        return
    log(f"plan: {len(jobs)} distinct builds covering {sum(1 + len(a) for _, _, a, _ in jobs)} images -> {args.namespace}-<v>:<task> (push={args.push})")

    status = Status(ROOT / "status.json")
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(build, v, t, a, args, status, k): (v, t, k) for v, t, a, k in jobs}
        for f in as_completed(futs):
            try:
                good = f.result()
            except Exception as e:
                log(f"{futs[f]}: EXCEPTION {e}")
                good = False
            ok += good
            fail += not good
    log(f"DONE ok={ok} fail={fail}")
    for k, v in status.data.items():
        if not v.get("ok"):
            print("  failed:", k)
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
