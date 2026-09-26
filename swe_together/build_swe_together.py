#!/usr/bin/env python3
"""Build, gold-validate and push native linux/arm64 images for SWE-Together (Togetherbench/SWE-Together @891d19e).

Per task (canonical_full109.json):
  1. copy tasks/<id>/environment to work/<id>/, patch the Dockerfile with arm64_rules.py;
  2. docker build --platform linux/arm64 -t bdqnghi/swe-together:<id>;
  3. validate.py: apply reference_patch.json as the judge does, run tests/test.sh as Harbor's verifier does,
     require reward 1.0;
  4. push, confirm the tag on Docker Hub, remove the local tag (unless --keep).
Resumable through status.json (per task: built / validated / pushed / reward / error). Logs in logs/<id>.log.

  python -u build_swe_together.py --jobs 7 --push                # everything not done yet
  python -u build_swe_together.py --task cli-task-0ec2e9 --force  # one task again
  python -u build_swe_together.py --only-dockerfile               # skip the tasks on private bases
"""
import argparse, json, os, re, shutil, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import arm64_rules  # noqa: E402
import pins  # noqa: E402
import validate as V  # noqa: E402

SWT = V.SWT
lock = threading.Lock()
PUSHER = ThreadPoolExecutor(max_workers=3)
MIN_FREE_GB = 108
# Images in use by the Stage 1 pilot (tib configs/subsets/swe-together.json): never remove their local tags.
SUBSET = Path("/home/nghibui/codes/tib/configs/subsets/swe-together.json")
PROTECTED = {e["task"] for e in json.loads(SUBSET.read_text())["selected"]} if SUBSET.exists() else set()


def log(msg):
    with lock:
        print(time.strftime("%H:%M:%S"), msg, flush=True)


def run(cmd, logfile, timeout=None):
    with open(logfile, "ab") as f:
        f.write(f"\n$ {' '.join(cmd)}\n".encode())
        f.flush()
        try:
            return subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            f.write(b"\nTIMEOUT\n")
            return 124


class Status:
    def __init__(self, path):
        self.path = path
        self.data = json.load(open(path)) if path.exists() else {}

    def get(self, key):
        with lock:
            return dict(self.data.get(key) or {})

    def update(self, key, **kw):
        """Merge into the file under an flock, so several builder processes can share status.json."""
        import fcntl
        with lock, open(self.path.with_suffix(".lock"), "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            if self.path.exists():
                self.data = json.load(open(self.path))
            d = self.data.setdefault(key, {})
            d.update(kw, ts=time.strftime("%Y-%m-%dT%H:%M:%S"))
            tmp = self.path.with_suffix(".tmp")
            json.dump(dict(sorted(self.data.items())), open(tmp, "w"), indent=1)
            os.replace(tmp, self.path)


def free_gb():
    st = os.statvfs("/var/lib/docker" if os.access("/var/lib/docker", os.R_OK) else "/")
    return st.f_bavail * st.f_frsize / 1e9


def wait_for_disk():
    while free_gb() < MIN_FREE_GB:
        log(f"disk: {free_gb():.0f} GB free < {MIN_FREE_GB}; waiting")
        time.sleep(120)


def prepare(task):
    src = SWT / "tasks" / task / "environment"
    dst = HERE / "work" / task
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    df = dst / "Dockerfile"
    text, applied = arm64_rules.patch_dockerfile(task, df.read_text())
    text, notes = pins.apply(task, text, dst, pins.load(task), arm64_rules.SKIP_CONSTRAINTS.get(task, ()))
    applied += notes
    df.write_text(text)
    for extra, content in arm64_rules.TASK_FILES.get(task, {}).items():
        (dst / extra).write_text(content)
    return dst, applied


def remote_has(ref, arch="arm64"):
    """Tag present on Docker Hub (Hub API: not subject to the registry's pull rate limit)."""
    import urllib.request
    repo, tag = ref.rsplit(":", 1)
    try:
        with urllib.request.urlopen(f"https://hub.docker.com/v2/repositories/{repo}/tags/{tag}", timeout=30) as r:
            d = json.load(r)
        return any(i.get("architecture") == arch for i in d.get("images", []))
    except Exception:  # noqa: BLE001
        return False


def official_run(task):
    img = V.task_config(task)["environment"]["docker_image"]
    for _ in range(4):
        if subprocess.run(["docker", "image", "inspect", img], capture_output=True).returncode == 0:
            break
        subprocess.run(["docker", "pull", "-q", "--platform", "linux/amd64", img], capture_output=True)
    return V.validate(task, img, "linux/amd64", HERE / "validation" / task / "official", timeout=4 * 3600)


def drop_official(task):
    """Remove the official amd64 image (pulled by fingerprint.py --keep / official_run) once the task is done.
    Only ghcr.io/togetherbench/multi-user-turn-codebench/* images, which this job pulled, and never one in use."""
    img = V.task_config(task)["environment"]["docker_image"]
    used = subprocess.run(["docker", "ps", "-a", "-q", "--filter", f"ancestor={img}"], capture_output=True,
                          text=True).stdout.strip()
    if not used:
        subprocess.run(["docker", "rmi", img], capture_output=True)


def process(task, args, status):
    ref = f"{args.namespace}:{task}"
    st = status.get(task)
    if not args.force and st.get("validated") and (st.get("pushed") or not args.push):
        return "done"
    logfile = HERE / "logs" / f"{task}.log"
    logfile.parent.mkdir(exist_ok=True)
    have_local = subprocess.run(["docker", "image", "inspect", ref], capture_output=True).returncode == 0
    if args.force or not (st.get("built") and have_local):
        fpp = HERE / "fingerprints" / f"{task}.json"
        for _ in range(240):  # fingerprint.py may still be running; the build needs it for pinning
            if fpp.exists():
                break
            time.sleep(30)
        wait_for_disk()
        ctx, applied = prepare(task)
        m = re.search(r"^FROM\s+(bdqnghi/swe-together-base:\S+)", (ctx / "Dockerfile").read_text(), re.M)
        for _ in range(360):  # the reconstructed base may still be building (build_bases.py)
            if not m or subprocess.run(["docker", "image", "inspect", m.group(1)], capture_output=True).returncode == 0 \
                    or remote_has(m.group(1)):
                break
            time.sleep(30)
        logfile.write_text("")
        t0 = time.time()
        log(f"{task}: building ({'; '.join(applied) or 'no changes'})")
        rc = 1
        for attempt in range(1 + args.retries):
            rc = run(["docker", "build", "--platform", "linux/arm64", "--progress=plain", "-t", ref, str(ctx)],
                     logfile, timeout=args.timeout)
            if rc == 0:
                break
            log(f"{task}: build failed; retry {attempt + 1} in 60s (network on this host is flaky)")
            time.sleep(60)
        dur = round(time.time() - t0)
        if rc != 0:
            log(f"{task}: BUILD FAILED ({dur}s) see {logfile}")
            status.update(task, built=False, validated=False, pushed=False, build_seconds=dur,
                          error="build failed", deviations=applied)
            return "build_failed"
        status.update(task, built=True, build_seconds=dur, deviations=applied, error=None)
        log(f"{task}: built ({dur}s)")
    if args.no_validate:
        return "built"
    if st.get("validated") and have_local and not args.force and args.push:
        return PUSHER.submit(push, task, ref, args, status, logfile)  # validated earlier, only the push is missing
    res = V.validate(task, ref)
    ok = V.passed(res)
    upd = dict(validated=ok, reward=res.get("reward"), mode=res["mode"], validated_as=res["user"],
               validate_seconds=res.get("seconds"), error=None if ok else (res.get("error") or f"reward {res.get('reward')}"))
    log(f"{task}: arm64 {res['mode']} reward={res.get('reward')} {'PASS' if ok else 'FAIL ' + str(res.get('error'))}")
    if not ok and not args.no_official:
        # Does the official amd64 image (under qemu) give the same result? Then it is a dataset property.
        off = official_run(task)
        upd["official_reward"] = off.get("reward")
        upd["official_error"] = off.get("error")
        same = res.get("reward") is not None and off.get("reward") is not None and \
            abs(float(res["reward"]) - float(off["reward"])) < 1e-6
        log(f"{task}: official amd64 reward={off.get('reward')} err={off.get('error')} -> "
            f"{'MATCHES official' if same else 'DIFFERS from official'}")
        if same:
            ok = True
            upd.update(validated=True, matches_official=True,
                       error=None, note=f"{res['mode']} reward {res['reward']} equals the official amd64 image's")
    status.update(task, **upd)
    drop_official(task)
    if not ok:
        return "validate_failed"
    if args.push:
        return PUSHER.submit(push, task, ref, args, status, logfile)  # pushes are bandwidth-bound: own pool
    return "validated"


def push(task, ref, args, status, logfile):
    pushed = False
    for attempt in range(4):
        rc = run(["docker", "push", ref], logfile)
        pushed = rc == 0 and remote_has(ref)
        if pushed:
            break
        log(f"{task}: push failed; retry {attempt + 1} in 60s")
        time.sleep(60)
    digest = subprocess.run(["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", ref],
                            capture_output=True, text=True).stdout.strip()
    status.update(task, pushed=pushed, repo_digests=digest)
    log(f"{task}: {'pushed' if pushed else 'PUSH FAILED'}")
    if pushed and not args.keep and task not in PROTECTED:
        subprocess.run(["docker", "rmi", ref], capture_output=True)
    return "pushed" if pushed else "push_failed"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", action="append", help="only these task ids; repeatable")
    ap.add_argument("--namespace", default="bdqnghi/swe-together")
    ap.add_argument("--jobs", type=int, default=7)
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep images locally after push")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-validate", action="store_true")
    ap.add_argument("--no-official", action="store_true", help="do not compare failures with the official image")
    ap.add_argument("--only-dockerfile", action="store_true", help="skip tasks built on the private ghcr bases")
    ap.add_argument("--only-bases", action="store_true", help="only tasks built on the private ghcr bases")
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=3 * 3600)
    args = ap.parse_args()
    tasks = json.load(open(SWT / "canonical_full109.json"))["tasks"]

    def on_base(t):
        return re.search(r"^FROM\s+ghcr\.io/togetherbench/togetherbench/", (SWT / "tasks" / t / "environment" /
                                                                          "Dockerfile").read_text(), re.M) is not None
    if args.only_dockerfile:
        tasks = [t for t in tasks if not on_base(t)]
    if args.only_bases:
        tasks = [t for t in tasks if on_base(t)]
    if args.task:
        tasks = [t for t in tasks if t in set(args.task)]
    log(f"plan: {len(tasks)} tasks -> {args.namespace} (push={args.push}, jobs={args.jobs})")
    status = Status(HERE / "status.json")
    counts = {}
    pending = []
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(process, t, args, status): t for t in tasks}
        for f in as_completed(futs):
            try:
                r = f.result()
                if hasattr(r, "result"):
                    pending.append((futs[f], r))
                    r = "validated"
            except Exception as e:  # noqa: BLE001
                log(f"{futs[f]}: EXCEPTION {e!r}")
                status.update(futs[f], error=f"exception: {e!r}")
                r = "exception"
            counts[r] = counts.get(r, 0) + 1
    for t, pf in pending:
        try:
            r = pf.result()
        except Exception as e:  # noqa: BLE001
            r = f"push exception {e!r}"
        counts[r] = counts.get(r, 0) + 1
    log(f"DONE {counts}")


if __name__ == "__main__":
    main()
