#!/usr/bin/env python3
"""Build, validate and push native linux/arm64 images for SWE-Interact (scaleapi/SWE-Interact, 75 multi-turn tasks).

Pipeline per task (resumable, state in status.json, logs in logs/<task>.log):
  1. prepare: copy the task's environment/ directory to work/<task>/ctx and apply the family rule
     (swebenchpro: FROM jefzda/sweap-images:<tag> -> bdqnghi/sweap-images:<tag>; deepswe: build as is;
     rf: FROM ghcr.io/scaleapi/swe-atlas:<tag>@sha256 -> bdqnghi/swe-interact-base:<name>) plus any per-task
     override in rules/<task>/ (Dockerfile.patch applied with `patch -p1`, files/ copied into the context).
  2. build:   docker build --platform linux/arm64 -t bdqnghi/swe-interact:<tag> work/<task>/ctx
  3. validate (validate.py): reference solution + the task's own verifier (tests/test.sh) inside the image.
  4. push, record the pushed digest, remove the local tag (unless --keep).

The tag is the task directory name; the three swebenchpro names that exceed Docker's 128-character tag limit
lose their `instance_` infix (see tag_for()).

Usage:
  build.py [--family swebenchpro|deepswe|rf] [--task NAME ...] [--jobs 4] [--no-push] [--keep] [--force]
           [--stage build|validate|push]  (default: all stages)
"""
import argparse, fcntl, json, os, re, shutil, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# a pristine export of upstream b32f98c (`git archive b32f98c | tar -x -C work/upstream`): the shared checkout may
# have swe-interact-arm64.patch applied, and the rules here rewrite the upstream files
SRC = Path(os.environ.get("SWE_INTERACT_SRC", str(Path(__file__).resolve().parent / "work" / "upstream")))
UPSTREAM_CHECKOUT = os.environ.get("SWE_INTERACT_CHECKOUT", "/home/nghibui/codes/tib/third_party/swe-interact")
if not (SRC / "data").exists() and "SWE_INTERACT_SRC" not in os.environ:
    SRC.mkdir(parents=True, exist_ok=True)
    subprocess.run(f"git -C {UPSTREAM_CHECKOUT} archive b32f98c | tar -x -C {SRC}", shell=True, check=True)
TASKS = SRC / "data" / "multiturn"
NS = "bdqnghi/swe-interact"
BASE_NS = "bdqnghi/swe-interact-base"
lock = threading.Lock()
# Builds pull public base images anonymously: the logged-in account's Docker Hub pull quota is shared with other
# jobs on this host and runs out (429); pushes use the normal (authenticated) configuration.
ANON = ROOT / "work" / "anon-docker-config"
ANON.mkdir(parents=True, exist_ok=True)
(ANON / "config.json").exists() or (ANON / "config.json").write_text("{}")
ANON_ENV = {**os.environ, "DOCKER_CONFIG": str(ANON)}

sys.path.insert(0, str(ROOT))
import validate as V  # noqa: E402


def log(msg):
    with lock:
        print(time.strftime("%H:%M:%S"), msg, flush=True)


def family(task):
    return task.split("_", 1)[0]


def tag_for(task):
    t = task.replace("swebenchpro_instance_", "swebenchpro_") if len(task) > 128 else task
    assert len(t) <= 128, t
    return t


def image_for(task):
    return f"{NS}:{tag_for(task)}"


def rf_base_name(task):
    """ghcr.io/scaleapi/swe-atlas:swe_atlas_RF_<org>_<repo>_<id>_1.0@sha256:... -> rf_<org>_<repo>_<id> (lowercased)."""
    ref = official_from(task)
    m = re.search(r"swe-atlas:swe_atlas_RF_(.+?)_1\.0@", ref)
    return "rf_" + m.group(1).lower()


def official_from(task):
    df = (TASKS / task / "environment" / "Dockerfile").read_text()
    return re.search(r"^FROM\s+(\S+)", df, re.M).group(1)


class Status:
    """status.json shared by concurrent build.py processes: every update re-reads the file under an flock."""

    def __init__(self, path):
        self.path = path
        self.lockf = path.with_suffix(".lock")

    def _load(self):
        return json.load(open(self.path)) if self.path.exists() else {}

    @property
    def data(self):
        return self._load()

    def get(self, task):
        return self._load().get(task, {})

    def update(self, task, **kw):
        with lock, open(self.lockf, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            data = self._load()
            d = data.setdefault(task, {"family": family(task), "image": image_for(task)})
            d.update(kw)
            d["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            tmp = self.path.with_suffix(f".tmp{os.getpid()}")
            json.dump(dict(sorted(data.items())), open(tmp, "w"), indent=1)
            os.replace(tmp, self.path)


def run(cmd, logfile, timeout=None, **kw):
    with open(logfile, "ab") as f:
        f.write(f"\n$ {' '.join(map(str, cmd))}\n".encode())
        f.flush()
        try:
            return subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=timeout, **kw).returncode
        except subprocess.TimeoutExpired:
            f.write(b"\n!! TIMEOUT\n")
            return 124


def prepare(task):
    ctx = ROOT / "work" / task / "ctx"
    if ctx.exists():
        shutil.rmtree(ctx)
    shutil.copytree(TASKS / task / "environment", ctx)
    df = ctx / "Dockerfile"
    s = df.read_text()
    fam = family(task)
    if fam == "swebenchpro":
        s = re.sub(r"^FROM jefzda/sweap-images:", "FROM bdqnghi/sweap-images:", s, flags=re.M)
    elif fam == "rf":
        s = re.sub(r"^FROM ghcr\.io/scaleapi/swe-atlas:\S+", f"FROM {BASE_NS}:{rf_base_name(task)}", s, flags=re.M)
    df.write_text(s)
    rules = ROOT / "rules" / task
    if (rules / "files").is_dir():
        shutil.copytree(rules / "files", ctx, dirs_exist_ok=True)
    if (rules / "Dockerfile.patch").exists():
        subprocess.run(["patch", "-p1", "-d", str(ctx), "-i", str(rules / "Dockerfile.patch")], check=True,
                       capture_output=True)
    add_constraints(ctx)
    if (rules / "Dockerfile.append").exists():  # extra arm64 steps appended to the task Dockerfile
        with open(ctx / "Dockerfile", "a") as f:
            f.write("\n" + (rules / "Dockerfile.append").read_text())
    if (rules / "prepare.sh").exists():  # generated inputs too large for git (e.g. transplanted caches)
        subprocess.run(["bash", str(rules / "prepare.sh"), str(ctx)], check=True)
    return ctx


def add_constraints(ctx):
    """If the rule shipped constraints.txt (tools/pin_python.py), make every pip/uv install in the build honour it."""
    if not (ctx / "constraints.txt").exists():
        return
    df = ctx / "Dockerfile"
    lines = df.read_text().splitlines()
    if any("swi-constraints" in l for l in lines):
        return
    i = next(i for i, l in enumerate(lines) if l.startswith("FROM "))
    lines[i + 1:i + 1] = ["COPY constraints.txt /opt/swi-constraints.txt",
                          "ENV PIP_CONSTRAINT=/opt/swi-constraints.txt UV_CONSTRAINT=/opt/swi-constraints.txt"]
    df.write_text("\n".join(lines) + "\n")


def remove_local_image(ref, reason):
    """Remove a local tag only if its remote copy exists and no container (running or stopped) uses the image.
    Every removal is appended to cleanup_log.jsonl (image, size, remote verified)."""
    r = subprocess.run(["docker", "image", "inspect", ref, "--format", "{{.Id}} {{.Size}}"], capture_output=True, text=True)
    if r.returncode:
        return False
    img_id, size = r.stdout.split()
    used = subprocess.run(["docker", "ps", "-a", "--filter", f"ancestor={img_id}", "-q"], capture_output=True, text=True).stdout.strip()
    if used:
        return False
    # remote verified: the image carries a RepoDigest for this repository (set by a successful push or pull of that
    # manifest), or the registry answers for the tag
    repo = ref.rsplit(":", 1)[0]
    rd = subprocess.run(["docker", "image", "inspect", ref, "--format", "{{json .RepoDigests}}"], capture_output=True,
                        text=True).stdout
    how = "repo digest " + next((d for d in json.loads(rd or "[]") if d.startswith(repo + "@")), "")
    if how == "repo digest ":
        if subprocess.run(["docker", "manifest", "inspect", ref], capture_output=True).returncode:
            return False
        how = "manifest inspect"
    if subprocess.run(["docker", "rmi", ref], capture_output=True).returncode:
        return False
    with lock, open(ROOT / "cleanup_log.jsonl", "a") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "image": ref, "size_gb": round(int(size) / 1e9, 2),
                            "remote_verified": how, "reason": reason}) + "\n")
    return True


def remote_digest(ref):
    r = subprocess.run(["docker", "buildx", "imagetools", "inspect", ref, "--format", "{{json .Manifest}}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return json.loads(r.stdout).get("digest")


def wait_for_disk(task, min_gb=110):
    """Do not start a build while the shared disk has less than min_gb free (the job must leave >= 100 GB)."""
    while shutil.disk_usage("/").free / 1e9 < min_gb:
        log(f"{task}: waiting for disk ({shutil.disk_usage('/').free / 1e9:.0f} GB free < {min_gb})")
        time.sleep(300)


def process(task, args, status):
    st = status.get(task)
    ref = image_for(task)
    logfile = ROOT / "logs" / f"{task}.log"
    logfile.parent.mkdir(exist_ok=True)
    stages = args.stage or ["build", "validate", "push"]
    if not args.force and st.get("pushed") and st.get("validated") and "push" in stages:
        log(f"{task}: done previously")
        return True
    have_local = subprocess.run(["docker", "image", "inspect", ref], capture_output=True).returncode == 0
    just_built = False

    if "build" in stages and (args.force or not (st.get("built") and have_local)):
        if family(task) == "rf":
            base = f"{BASE_NS}:{rf_base_name(task)}"
            if subprocess.run(["docker", "image", "inspect", base], capture_output=True).returncode != 0 \
                    and not remote_digest(base):
                log(f"{task}: base {base} missing (run build_rf_base.py first)")
                status.update(task, built=False, error=f"base {base} missing")
                return False
        wait_for_disk(task)
        ctx = prepare(task)
        log(f"{task}: building")
        t0 = time.time()
        for attempt in range(6):  # retry transient registry/DNS failures and Docker Hub 429 (pull quota) answers
            rc = run(["docker", "build", "--platform", "linux/arm64", "--progress=plain", "-t", ref, str(ctx)],
                     logfile, timeout=args.timeout, env=ANON_ENV)
            tail = logfile.read_text(errors="replace")[-3000:]
            if rc == 0 or not re.search(r"dial tcp|i/o timeout|TLS handshake|server misbehaving|connection reset|"
                                        r"failed to fetch oauth|503 Service|502 Bad|EOF$|429 Too Many", tail, re.M):
                break
            time.sleep(120 * (attempt + 1))
        dur = round(time.time() - t0)
        if rc != 0:
            log(f"{task}: BUILD FAILED ({dur}s) see {logfile}")
            status.update(task, built=False, validated=None, pushed=False, error=f"build rc={rc}", build_s=dur)
            return False
        status.update(task, built=True, validated=None, pushed=False, error=None, build_s=dur)
        have_local = just_built = True
        log(f"{task}: built ({dur}s)")

    if "validate" in stages and (args.force or just_built or status.get(task).get("validated") is not True):
        if not have_local:
            log(f"{task}: no local image to validate")
            return False
        log(f"{task}: validating")
        t0 = time.time()
        res = V.validate(task, ref, logfile)
        dur = round(time.time() - t0)
        ok = res["ok"]
        status.update(task, validated=ok, validation=res, validate_s=dur,
                      error=None if ok else f"validation: {res.get('reason')}")
        log(f"{task}: validation {'PASS' if ok else 'FAIL'} ({dur}s) {res.get('reason', '')}")
        if not ok:
            return False

    if "push" in stages and not args.no_push:
        if status.get(task).get("validated") is not True and not args.push_unvalidated:
            log(f"{task}: not validated, not pushing")
            return False
        for attempt in range(4):  # Docker Hub TLS handshake timeouts are transient
            rc = run(["docker", "push", ref], logfile)
            if rc == 0:
                break
            time.sleep(30 * (attempt + 1))
        if rc != 0:
            status.update(task, pushed=False, error="push failed")
            log(f"{task}: PUSH FAILED")
            return False
        # the digest docker push reports (registry lookups can hit Docker Hub's 429 pull quota)
        m = re.findall(r"digest: (sha256:[0-9a-f]{64}) size:", logfile.read_text(errors="replace")[-20000:])
        dig = m[-1] if m else remote_digest(ref)
        status.update(task, pushed=bool(dig), digest=dig, error=None if dig else "push: digest not found")
        log(f"{task}: pushed {dig}")
        if dig and not args.keep:
            remove_local_image(ref, "task image pushed")
            if family(task) == "swebenchpro":
                base = re.search(r"^FROM\s+(\S+)", (ROOT / "work" / task / "ctx" / "Dockerfile").read_text(), re.M).group(1)
                remove_local_image(base, f"base of {task}, task image pushed")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", action="append")
    ap.add_argument("--task", action="append")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--stage", action="append", choices=["build", "validate", "push"])
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--push-unvalidated", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep the local image after pushing")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--timeout", type=int, default=3 * 3600)
    args = ap.parse_args()
    tasks = sorted(p.name for p in TASKS.iterdir() if (p / "environment" / "Dockerfile").exists())
    if args.family:
        tasks = [t for t in tasks if family(t) in args.family]
    if args.task:
        tasks = [t for t in tasks if t in set(args.task)]
    status = Status(ROOT / "status.json")
    log(f"plan: {len(tasks)} tasks, jobs={args.jobs}, stages={args.stage or 'all'}")
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(process, t, args, status): t for t in tasks}
        for f in as_completed(futs):
            try:
                good = f.result()
            except Exception as e:  # keep going; record
                log(f"{futs[f]}: EXCEPTION {e!r}")
                status.update(futs[f], error=f"exception: {e!r}")
                good = False
            ok += good
            fail += not good
    log(f"DONE ok={ok} fail={fail}")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
