#!/usr/bin/env python3
"""Pin a deepswe_* task's Python packages to what the official prebuilt image resolved (drift control).

The task Dockerfile re-resolves pip packages at build time; months later newer releases break the hidden tests
(e.g. starlette deprecating httpx in its TestClient). task.toml's docker_image
(public.ecr.aws/d3j8x8q7/swe-bench-202605:<ext_id>) is the official amd64 build of the same Dockerfile. Its installed
distributions (*.dist-info, read from `docker export` without executing anything) become
rules/<task>/files/constraints.txt; build.py/make_tree.py then insert
  COPY constraints.txt /opt/swi-constraints.txt
  ENV PIP_CONSTRAINT=/opt/swi-constraints.txt UV_CONSTRAINT=/opt/swi-constraints.txt
after the FROM line. Build backends are left unpinned, editable installs of the repository itself are skipped.

Usage: pin_python.py <task> [<task> ...]
"""
import re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build as B  # noqa: E402

BUILD_BACKENDS = {'calver', 'cmake', 'cython', 'editables', 'expandvars', 'flit-core', 'hatch-fancy-pypi-readme',
                  'hatch-vcs', 'hatchling', 'maturin', 'meson', 'meson-python', 'ninja', 'packaging', 'pathspec',
                  'pdm-backend', 'pip', 'poetry-core', 'pybind11', 'scikit-build-core', 'setuptools', 'setuptools-rust',
                  'setuptools-scm', 'trove-classifiers', 'versioneer', 'wheel'}


def official_image(task):
    toml = (B.TASKS / task / "task.toml").read_text()
    return re.search(r'^docker_image\s*=\s*"([^"]+)"', toml, re.M).group(1)


def pin(task):
    img = official_image(task)
    subprocess.run(["docker", "pull", "-q", "--platform", "linux/amd64", img], check=True, stdout=subprocess.DEVNULL)
    c = subprocess.run(["docker", "create", "--platform", "linux/amd64", img, "true"], capture_output=True, text=True,
                       check=True).stdout.strip()
    try:
        names = subprocess.run(f"docker export {c} | tar -t", shell=True, capture_output=True, text=True).stdout
    finally:
        subprocess.run(["docker", "rm", c], capture_output=True)
    cons = {}
    for line in names.splitlines():
        m = re.match(r"^(?:\./)?(?:usr/local/lib|usr/lib)/python3[^/]*/(?:site|dist)-packages/([^/]+)-([^/-]+)\.dist-info/$",
                     line)
        if m:
            name = m.group(1).lower().replace("_", "-")
            if name not in BUILD_BACKENDS:
                cons[name] = m.group(2).split("+")[0]
    # the repository's own editable install carries a 0.0.0/dev version: drop it
    repo_pkg = re.search(r"github\.com/[^/]+/([^/ .]+)", (B.TASKS / task / "environment" / "Dockerfile").read_text())
    if repo_pkg:
        cons.pop(repo_pkg.group(1).lower(), None)
    out = ROOT / "rules" / task / "files" / "constraints.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(f"{k}=={v}\n" for k, v in sorted(cons.items())))
    subprocess.run(["docker", "rmi", img], capture_output=True)
    print(f"{task}: {len(cons)} pins from {img}")


if __name__ == "__main__":
    for t in sys.argv[1:]:
        pin(t)
