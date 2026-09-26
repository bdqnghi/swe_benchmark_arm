#!/usr/bin/env python3
"""Identify the public base image of a history-scrubbed swe-atlas image by exact diff_id prefix match.

Candidates are derived from the image's ENV (GOLANG_VERSION, NODE_VERSION, PYTHON_VERSION, JAVA_VERSION) and
/etc/os-release codename, plus any given with --candidate. For each candidate the linux/amd64 diff_ids are fetched
from the registry and the longest common prefix with the image's own diff_ids wins. The arm64 build then uses the
same tag (multi-arch) as its FROM.

Usage: find_base.py <image> [--candidate REF ...]   -> prints JSON {base, matched_layers, total_layers}
"""
import argparse, json, re, subprocess, sys


def diff_ids_local(image):
    return json.loads(subprocess.run(["docker", "image", "inspect", image, "--format", "{{json .RootFS.Layers}}"],
                                     capture_output=True, text=True, check=True).stdout)


def diff_ids_remote(ref):
    """Look Docker Hub library images up through mirror.gcr.io (same digests, no Docker Hub pull quota)."""
    import time
    if "/" not in ref.split(":")[0]:
        ref = "mirror.gcr.io/library/" + ref
    for attempt in range(6):  # Docker Hub answers 429 when the shared host exhausts its pull quota
        r = subprocess.run(["docker", "buildx", "imagetools", "inspect", ref, "--format", "{{json .Image}}"],
                           capture_output=True, text=True)
        if r.returncode == 0 or "429" not in r.stderr:
            break
        time.sleep(60 * (attempt + 1))
    if r.returncode:
        return None
    d = json.loads(r.stdout)
    d = d.get("linux/amd64", d)
    return (d.get("rootfs") or {}).get("diff_ids")


def env_of(image):
    cfg = json.loads(subprocess.run(["docker", "image", "inspect", image, "--format", "{{json .Config}}"],
                                    capture_output=True, text=True, check=True).stdout)
    return dict(e.split("=", 1) for e in cfg.get("Env") or [])


def os_release(image):
    c = subprocess.run(["docker", "create", "--platform", "linux/amd64", image, "true"], capture_output=True,
                       text=True, check=True).stdout.strip()
    try:
        out = subprocess.run(f"docker cp -L {c}:/etc/os-release - | tar xO", shell=True, capture_output=True,
                             text=True).stdout
    finally:
        subprocess.run(["docker", "rm", c], capture_output=True)
    return dict(re.findall(r'^(\w+)="?([^"\n]*)"?', out, re.M))


def candidates(image):
    env, osr = env_of(image), os_release(image)
    code = osr.get("VERSION_CODENAME", "")
    ver = osr.get("VERSION_ID", "")
    out = []
    alp = ""
    if osr.get("ID") == "alpine":
        code = "alpine" + ".".join(ver.split(".")[:2])
        alp = "alpine"
    if env.get("GOLANG_VERSION"):
        v = env["GOLANG_VERSION"]
        out += [f"golang:{v}-{code}", f"golang:{v}", f"golang:{v.rsplit('.', 1)[0]}-{code}"]
        if alp:
            out += [f"golang:{v}-alpine"]
    if env.get("NODE_VERSION"):
        v = env["NODE_VERSION"]
        out += [f"node:{v}-{code}", f"node:{v}", f"node:{v}-{code}-slim", f"node:{v}-slim",
                f"node:{v.split('.')[0]}-{code}", f"node:{v.split('.')[0]}-{code}-slim"]
    if env.get("PYTHON_VERSION"):
        v = env["PYTHON_VERSION"]
        out += [f"python:{v}-{code}", f"python:{v}-slim-{code}", f"python:{v}", f"python:{v}-slim"]
    if alp:
        out += [f"alpine:{ver}", f"alpine:{'.'.join(ver.split('.')[:2])}"]
        if env.get("NODE_VERSION"):
            out += [f"node:{env['NODE_VERSION']}-{code}", f"node:{env['NODE_VERSION']}-alpine"]
        if env.get("PYTHON_VERSION"):
            out += [f"python:{env['PYTHON_VERSION']}-{code}", f"python:{env['PYTHON_VERSION']}-alpine"]
    if osr.get("ID") == "debian":
        out += [f"buildpack-deps:{code}", f"buildpack-deps:{code}-scm", f"buildpack-deps:{code}-curl",
                f"debian:{code}", f"debian:{code}-slim"]
    if osr.get("ID") == "ubuntu":
        out += [f"ubuntu:{ver}", f"ubuntu:{code}", f"buildpack-deps:{code}", f"buildpack-deps:{code}-scm",
                f"buildpack-deps:{code}-curl"]
    return out, env, osr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--candidate", action="append", default=[])
    a = ap.parse_args()
    mine = diff_ids_local(a.image)
    cands, env, osr = candidates(a.image)
    best = (None, 0)
    tried = {}
    for c in a.candidate + cands:
        ids = diff_ids_remote(c)
        if not ids:
            tried[c] = None
            continue
        k = 0
        while k < min(len(ids), len(mine)) and ids[k] == mine[k]:
            k += 1
        full = k == len(ids)
        tried[c] = f"{k}/{len(ids)}"
        if full and k > best[1]:
            best = (c, k)
    print(json.dumps({"image": a.image, "base": best[0], "matched_layers": best[1], "total_layers": len(mine),
                      "os": osr.get("PRETTY_NAME"), "env": env, "tried": tried}, indent=1))


if __name__ == "__main__":
    main()
