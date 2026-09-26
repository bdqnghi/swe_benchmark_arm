#!/usr/bin/env python3
"""Write swe-together-arm64.patch: the SWE-Together task tree (Togetherbench/SWE-Together @891d19e) pointed at the
arm64 images, plus the arm64 Dockerfiles that built them.

For every canonical task that is pushed (status.json):
  * task.toml [environment] docker_image -> bdqnghi/swe-together:<task_id>
  * environment/Dockerfile               -> the patched Dockerfile that built the image (work/<id>/Dockerfile),
    plus environment/arm64-pip-constraints.txt when one was used
so that `run_eval.py --env-type docker` pulls the arm64 image and a local rebuild reproduces it.

  python make_dataset.py            # writes swe-together-arm64.patch
  git -C SWE-Together apply ../swe_together/swe-together-arm64.patch
"""
import json, re, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SWT = Path("/home/nghibui/codes/tib/third_party/swe-together")
COMMIT = "891d19e"


def main():
    status = json.loads((HERE / "status.json").read_text())
    tasks = json.loads((SWT / "canonical_full109.json").read_text())["tasks"]
    wt = HERE / "work" / "dataset"
    shutil.rmtree(wt, ignore_errors=True)
    subprocess.run(["git", "clone", "-q", "--shared", "--no-checkout", str(SWT), str(wt)], check=True)
    subprocess.run(["git", "-C", str(wt), "checkout", "-q", COMMIT], check=True)
    n = 0
    for t in tasks:
        st = status.get(t) or {}
        if not st.get("pushed"):
            print("not pushed, left unchanged:", t)
            continue
        toml = wt / "tasks" / t / "task.toml"
        text = toml.read_text()
        text, k = re.subn(r'^docker_image = ".*"$', f'docker_image = "bdqnghi/swe-together:{t}"', text, flags=re.M)
        assert k == 1, t
        toml.write_text(text)
        src = HERE / "work" / t
        if (src / "Dockerfile").exists():
            shutil.copy(src / "Dockerfile", wt / "tasks" / t / "environment" / "Dockerfile")
            for extra in src.glob("arm64-*"):
                shutil.copy(extra, wt / "tasks" / t / "environment" / extra.name)
        n += 1
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True)
    diff = subprocess.run(["git", "-C", str(wt), "diff", "--cached", "--binary"], capture_output=True, text=True,
                          check=True).stdout
    (HERE / "swe-together-arm64.patch").write_text(diff)
    print(f"{n}/{len(tasks)} tasks repointed; wrote swe-together-arm64.patch ({len(diff.splitlines())} lines)")


if __name__ == "__main__":
    sys.exit(main())
