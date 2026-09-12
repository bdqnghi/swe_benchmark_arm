#!/usr/bin/env python3
"""Pin a DeepSWE task's Python dependencies to what the official (amd64) pinned image resolved.

The environment/Dockerfile re-resolves pip packages at build time, so an arm64 rebuild months later drifts
(newer joblib/pytest/...) and the hidden tests fail. We pull the official verifier base image
(public.ecr.aws/d3j8x8q7/swe-bench-202605:<ext_id>-v1.1, recorded in task.toml metadata.ext_id), list its
installed dists (*.dist-info) and write environment/constraints.txt, then make the Dockerfile install with
PIP_CONSTRAINT/UV_CONSTRAINT pointing at it. Re-run build_deepswe.py --task <task> --force afterwards.

Usage: pin_task.py <task> [<task> ...]   (tasks under deep-swe/tasks/)
"""
import re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ECR = "public.ecr.aws/d3j8x8q7/swe-bench-202605"
SNAPSHOT_PIPS = r"""
for d in $(find / -xdev -maxdepth 7 -name '*.dist-info' -not -path '*/node_modules/*' 2>/dev/null); do
  if [ -f "$d/direct_url.json" ] && grep -q '"editable": *true' "$d/direct_url.json"; then continue; fi
  basename "$d"
done
"""
BUILD_BACKENDS = {'calver', 'cmake', 'cython', 'editables', 'expandvars', 'flit-core', 'hatch-fancy-pypi-readme', 'hatch-vcs', 'hatchling', 'maturin', 'meson', 'meson-python', 'ninja', 'packaging', 'pathspec', 'pdm-backend', 'pip', 'poetry-core', 'pybind11', 'scikit-build-core', 'setuptools', 'setuptools-rust', 'setuptools-scm', 'trove-classifiers', 'versioneer', 'wheel'}

MARK = "/opt/deepswe-constraints.txt"


def official_image(task_dir):
    toml = (task_dir / "task.toml").read_text()
    m = re.search(r'^ext_id\s*=\s*"([^"]+)"', toml, re.M)
    if not m:
        sys.exit(f"{task_dir}: no ext_id")
    return f"{ECR}:{m.group(1)}-v1.1"


def pin(task):
    tdir = ROOT / "deep-swe" / "tasks" / task
    img = official_image(tdir)
    print(f"{task}: pulling {img}", flush=True)
    subprocess.run(["docker", "pull", "-q", "--platform", "linux/amd64", img], check=True, stdout=subprocess.DEVNULL)
    out = subprocess.run(["docker", "run", "--rm", "--platform", "linux/amd64", "--entrypoint", "sh", img, "-c", SNAPSHOT_PIPS],
                         capture_output=True, text=True).stdout
    subprocess.run(["docker", "rmi", img], capture_output=True)
    cons = set()
    for line in out.split():
        m = re.match(r"(.+?)-([^-]+)\.dist-info$", line)
        if m and m.group(1).lower().replace("_", "-") not in BUILD_BACKENDS:
            cons.add(f"{m.group(1)}=={m.group(2).split('+')[0]}")
    if not cons:
        sys.exit(f"{task}: no dists found in {img}")
    env = tdir / "environment"
    (env / "constraints.txt").write_text("\n".join(sorted(cons)) + "\n")
    df = env / "Dockerfile"
    s = df.read_text()
    if MARK not in s:
        lines = s.splitlines()
        i = next(i for i, l in enumerate(lines) if l.startswith("FROM "))
        lines[i + 1:i + 1] = [f"COPY constraints.txt {MARK}", f"ENV PIP_CONSTRAINT={MARK} UV_CONSTRAINT={MARK}"]
        df.write_text("\n".join(lines) + "\n")
    print(f"{task}: {len(cons)} pins written", flush=True)


if __name__ == "__main__":
    for t in sys.argv[1:]:
        pin(t)
