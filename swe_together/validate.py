#!/usr/bin/env python3
"""Gold (reference-patch) validation of one SWE-Together task image.

Mirrors what the SWE-Together harness does after a trial:
  * the judge sandbox (eval/correctness/sandbox.py) applies a patch as root to the task repo: repo = the
    recorder's banner hint or the shallowest .git under the well-known roots; candidates from
    src/patch_normalize.apply_candidates, tried with `git apply --check` (then `-C2`); chmod -R a+rwX;
  * Harbor's verifier (external/harbor/src/harbor/verifier/verifier.py) uploads tests/ to /tests and runs
    `bash -c "/tests/test.sh > /logs/verifier/test-stdout.txt 2>&1"`, then reads /logs/verifier/reward.txt
    (else reward.json). The run is as root, as on the harness's default E2B sandbox (e2b.py: user="root")
    and on enroot; Harbor's docker backend instead execs as the image's USER (see NOTES.md).
Tasks whose reference_patch.json has no `patch` (16 of 109, `_status: no_canonical`) cannot be gold-validated;
they run test.sh on the unpatched repo ("baseline" mode) so the result can be compared with the official image.
The container gets the task's cpus/memory limits (Harbor's compose deploy limits) and default networking.

Usage: validate.py <task_id> [--image IMG] [--platform linux/amd64] [--out DIR]
Prints a JSON result; exit 0 when reward == 1.0.
"""
import argparse, json, os, shlex, subprocess, sys, tempfile, time, tomllib, uuid
from pathlib import Path

SWT = Path(os.environ.get("SWT_ROOT", "/home/nghibui/codes/tib/third_party/swe-together"))
sys.path.insert(0, str(SWT / "src"))
from patch_normalize import apply_candidates, main_repo_path  # noqa: E402

HERE = Path(__file__).resolve().parent

APPLY_SH = r'''set -e
HINT=%(hint)s
ROOTS="/workspace /opt /home /app /repo /tmp /entire-cli /entireio-cli /no-magic"
EXISTING=""
for r in $ROOTS; do [ -e "$r" ] && EXISTING="$EXISTING $r"; done
if [ -z "$EXISTING" ]; then echo "NO_REPO_ROOTS_EXIST" >&2; exit 1; fi
REPO=""
if [ -n "$HINT" ] && [ -e "$HINT/.git" ]; then REPO="$HINT"; fi
if [ -z "$REPO" ]; then
  REPO=$(find $EXISTING -maxdepth 3 -name .git \( -type d -o -type f \) 2>/dev/null | while IFS= read -r g; do printf "%%d %%s\n" "${#g}" "$g"; done | sort -n | head -1 | cut -d" " -f2- | xargs -I{} dirname {})
fi
if [ -z "$REPO" ]; then echo "NO_GIT_REPO_FOUND" >&2; exit 1; fi
cd "$REPO" && echo "applying to $(pwd)"
APPLIED=""
for flags in "" "-C2"; do
  for i in $(seq 0 %(last)d); do
    if git -c safe.directory="*" apply --check --whitespace=nowarn $flags "/tmp/agent.patch.$i" 2>/dev/null; then
      git -c safe.directory="*" apply --whitespace=nowarn $flags "/tmp/agent.patch.$i" && cp "/tmp/agent.patch.$i" /tmp/agent.patch && APPLIED="$i${flags:+ $flags}"; break 2
    fi
  done
done
if [ -z "$APPLIED" ]; then git -c safe.directory="*" apply --check --whitespace=nowarn /tmp/agent.patch.0; exit 1; fi
echo "applied candidate $APPLIED"
{ chmod -R a+rwX "$REPO" 2>/dev/null || true; }
'''


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def task_config(task):
    return tomllib.loads((SWT / "tasks" / task / "task.toml").read_text())


def has_patch(task):
    return bool((json.loads((SWT / "tasks" / task / "reference_patch.json").read_text()).get("patch") or "").strip())


def validate(task, image, platform="linux/arm64", out=None, timeout=None, limits=True, user="root"):
    tdir = SWT / "tasks" / task
    cfg = task_config(task)
    env = cfg.get("environment", {})
    vtimeout = timeout or max(1800, int(3 * float(cfg.get("verifier", {}).get("timeout_sec", 600))))
    out = Path(out or HERE / "validation" / task)
    out.mkdir(parents=True, exist_ok=True)
    name = f"swt-val-{task[:40]}-{uuid.uuid4().hex[:6]}"
    res = {"task": task, "image": image, "platform": platform, "reward": None, "error": None, "user": user,
           "mode": "gold" if has_patch(task) else "baseline"}
    t0 = time.time()
    run = ["docker", "run", "-d", "--name", name, "--platform", platform]
    if limits:
        if env.get("cpus"):
            run += ["--cpus", str(env["cpus"])]
        if env.get("memory"):
            run += ["--memory", str(env["memory"])]
    run += [image, "sh", "-c", "sleep infinity"]
    r = sh(run)
    if r.returncode:
        res["error"] = "container start failed: " + r.stderr.strip()[-500:]
        return res
    try:
        ref = json.loads((tdir / "reference_patch.json").read_text())
        patch = ref.get("patch") or ""
        cands = apply_candidates(patch) or [""]
        if res["mode"] == "baseline":
            cands = []
        with tempfile.TemporaryDirectory() as td:
            for i, c in enumerate(cands):
                p = Path(td) / f"agent.patch.{i}"
                p.write_text(c)
                sh(["docker", "cp", str(p), f"{name}:/tmp/agent.patch.{i}"], check=True)
            if cands:
                script = APPLY_SH % {"hint": shlex.quote(main_repo_path(patch) or ""), "last": len(cands) - 1}
                a = sh(["docker", "exec", "-u", "root", name, "bash", "-c", script], timeout=600)
                (out / "apply.log").write_text(a.stdout + a.stderr)
        if cands and a.returncode:
            res["error"] = "patch_apply_failed: " + (a.stdout + a.stderr).strip()[-400:]
            return res
        # upload tests/ -> /tests (Harbor upload_dir), make /logs/verifier writable for the default user
        sh(["docker", "exec", "-u", "root", name, "bash", "-c", "rm -rf /tests && mkdir -p /logs/verifier /logs/agent"])
        sh(["docker", "cp", f"{tdir / 'tests'}/.", f"{name}:/tests"], check=True)
        sh(["docker", "exec", "-u", "root", name, "bash", "-c",
            "chmod -R a+rwX /tests /logs && chmod +x /tests/test.sh"])
        t1 = time.time()
        try:
            v = sh(["docker", "exec"] + (["-u", user] if user else []) + [name, "bash", "-c",
                    "/tests/test.sh > /logs/verifier/test-stdout.txt 2>&1"], timeout=vtimeout)
            res["test_exit"] = v.returncode
        except subprocess.TimeoutExpired:
            res["error"] = f"verifier timeout after {vtimeout}s"
        res["test_seconds"] = round(time.time() - t1)
        vdir = out / "verifier"
        sh(["rm", "-rf", str(vdir)])
        sh(["docker", "cp", f"{name}:/logs/verifier", str(vdir)])
        rt, rj = vdir / "reward.txt", vdir / "reward.json"
        try:
            if rt.exists():
                res["reward"] = float(rt.read_text().strip())
            elif rj.exists():
                res["reward"] = json.loads(rj.read_text())
            elif not res["error"]:
                res["error"] = "no reward file"
        except Exception as e:  # noqa: BLE001
            res["error"] = f"reward parse: {e}"
        so = vdir / "test-stdout.txt"
        if so.exists():
            res["tail"] = so.read_text(errors="replace")[-600:]
    finally:
        sh(["docker", "rm", "-f", name])
        res["seconds"] = round(time.time() - t0)
        (out / f"result-{platform.replace('/', '_')}.json").write_text(json.dumps(res, indent=1))
    return res


def passed(res):
    r = res.get("reward")
    if isinstance(r, dict):
        r = r.get("reward")
    return r is not None and float(r) >= 1.0 - 1e-9


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--image")
    ap.add_argument("--platform", default="linux/arm64")
    ap.add_argument("--out")
    ap.add_argument("--timeout", type=int)
    ap.add_argument("--no-limits", action="store_true")
    ap.add_argument("--user", default="root", help="user for test.sh ('' = image USER, as Harbor's docker backend)")
    a = ap.parse_args()
    image = a.image or f"bdqnghi/swe-together:{a.task}"
    res = validate(a.task, image, a.platform, a.out, a.timeout, not a.no_limits, a.user)
    print(json.dumps({k: v for k, v in res.items() if k != "tail"}, indent=1))
    if not passed(res) and res.get("tail"):
        print(res["tail"])
    sys.exit(0 if passed(res) else 1)


if __name__ == "__main__":
    main()
