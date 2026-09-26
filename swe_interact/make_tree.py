#!/usr/bin/env python3
"""Write the arm64 copy of the SWE-Interact task tree and the patch that produces it.

For every task in data/multiturn and data/singleturn:
  * task.toml `[environment] docker_image` -> bdqnghi/swe-interact:<tag> (the multi-turn image; it is the single-turn
    image plus /usr/local/bin/repo_exec_server.py, which the single-turn run never starts). Harbor's verifier runs in
    the shared (agent) container for these tasks, so no [verifier.environment] entry is needed.
  * environment/Dockerfile -> the arm64 recipe (same rewrite rules as build.py), so `--force-build` also builds natively.
    Generated inputs too large for git (rules/<task>/prepare.sh outputs) are not copied; pull the image instead.

Usage: make_tree.py [--out tasks_arm64] [--patch swe-interact-arm64.patch] [--only-built]
Then:  harbor run -p "$PWD/tasks_arm64/multiturn" -e docker --no-force-build ...
"""
import argparse, json, re, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import build as B  # noqa: E402


def set_docker_image(toml, image):
    lines = toml.splitlines()
    try:
        i = lines.index("[environment]")
    except ValueError:
        lines += ["", "[environment]"]
        i = len(lines) - 1
    j = i + 1
    while j < len(lines) and not lines[j].startswith("["):
        if re.match(r"\s*docker_image\s*=", lines[j]):
            lines[j] = f'docker_image = "{image}"'
            return "\n".join(lines) + "\n"
        j += 1
    lines.insert(i + 1, f'docker_image = "{image}"')
    return "\n".join(lines) + "\n"


def arm64_env_dir(task, variant_env):
    """Apply build.py's rewrite rules to a (multi- or single-turn) environment/ dir copy."""
    df = variant_env / "Dockerfile"
    s = df.read_text()
    fam = B.family(task)
    if fam == "swebenchpro":
        s = re.sub(r"^FROM jefzda/sweap-images:", "FROM bdqnghi/sweap-images:", s, flags=re.M)
    elif fam == "rf":
        s = re.sub(r"^FROM ghcr\.io/scaleapi/swe-atlas:\S+", f"FROM {B.BASE_NS}:{B.rf_base_name(task)}", s, flags=re.M)
    df.write_text(s)
    rules = ROOT / "rules" / task
    if (rules / "files").is_dir():
        shutil.copytree(rules / "files", variant_env, dirs_exist_ok=True)
    if (rules / "Dockerfile.patch").exists():
        r = subprocess.run(["patch", "-p1", "-d", str(variant_env), "-i", str(rules / "Dockerfile.patch")],
                           capture_output=True, text=True)
        if r.returncode:
            print(f"warning: {task}: Dockerfile.patch does not apply to {variant_env}: {r.stdout}", file=sys.stderr)
    B.add_constraints(variant_env)
    if (rules / "Dockerfile.append").exists():
        with open(variant_env / "Dockerfile", "a") as f:
            f.write("\n" + (rules / "Dockerfile.append").read_text())
    for p in variant_env.glob("*.orig"):
        p.unlink()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "tasks_arm64"))
    ap.add_argument("--patch", default=str(ROOT / "swe-interact-arm64.patch"))
    ap.add_argument("--only-pushed", action="store_true", help="rewrite only tasks whose image is pushed")
    a = ap.parse_args()
    out = Path(a.out)
    status = B.Status(ROOT / "status.json").data
    with tempfile.TemporaryDirectory() as td:
        orig = Path(td) / "a"
        new = Path(td) / "b"
        for variant in ("multiturn", "singleturn"):
            shutil.copytree(B.SRC / "data" / variant, orig / variant)
            shutil.copytree(B.SRC / "data" / variant, new / variant)
        n = 0
        for variant in ("multiturn", "singleturn"):
            for tdir in sorted((new / variant).iterdir()):
                task = tdir.name
                if a.only_pushed and not status.get(task, {}).get("pushed"):
                    continue
                tt = tdir / "task.toml"
                tt.write_text(set_docker_image(tt.read_text(), B.image_for(task)))
                arm64_env_dir(task, tdir / "environment")
                n += 1
        # diff as a patch against the SWE-Interact checkout root (apply with `git apply`): commit the originals in a
        # scratch repository, overlay the rewritten tree, diff the index
        repo = Path(td) / "repo"
        shutil.copytree(orig, repo / "data")
        g = lambda *c: subprocess.run(["git", "-c", "user.name=x", "-c", "user.email=x@x", *c], cwd=repo,
                                      capture_output=True, text=True, check=True).stdout
        g("init", "-q")
        g("add", "-A")
        g("commit", "-qm", "upstream")
        shutil.rmtree(repo / "data")
        shutil.copytree(new, repo / "data")
        g("add", "-A")
        diff = g("diff", "--cached", "--binary")
        Path(a.patch).write_text(diff)
        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(new, out)
    print(f"rewrote {n} task dirs -> {out}; patch {a.patch} ({len(diff.splitlines())} lines)")


if __name__ == "__main__":
    main()
