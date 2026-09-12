#!/usr/bin/env python3
"""Fingerprint one image per base group: distro, toolchain versions, key libs.

  python3 fingerprint.py official   -> pulls jefzda/sweap-images (amd64) for the first instance of each base,
                                       writes fingerprints/official.json, removes each image after inspecting
  python3 fingerprint.py mine       -> same for bdqnghi/sweap-images (arm64) images that exist in status.json
  python3 fingerprint.py compare    -> prints differences between the two
"""
import json, subprocess, sys, re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from build import load_plan, local_name

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "fingerprints"
OUT.mkdir(exist_ok=True)

PROBE = r'''
. /etc/os-release 2>/dev/null; echo "os=$PRETTY_NAME"
echo "arch=$(uname -m)"
echo "python=$(python --version 2>&1 || python3 --version 2>&1)"
echo "pip=$(pip --version 2>&1 | cut -d' ' -f2)"
echo "go=$(go version 2>/dev/null | awk '{print $3}')"
echo "node=$(node --version 2>/dev/null)"
echo "npm=$(npm --version 2>/dev/null)"
echo "yarn=$(yarn --version 2>/dev/null)"
echo "pnpm=$(pnpm --version 2>/dev/null)"
echo "rust=$(rustc --version 2>/dev/null)"
echo "java=$(java -version 2>&1 | head -1)"
echo "taglib=$(taglib-config --version 2>/dev/null)"
echo "chrome=$(google-chrome --version 2>/dev/null | head -1)"
echo "chromedriver=$(chromedriver --version 2>/dev/null | head -1)"
echo "gcc=$(gcc --version 2>/dev/null | head -1)"
echo "app_head=$(git -C /app rev-parse HEAD 2>/dev/null)"
echo "pip_pkgs=$(pip list --format=freeze 2>/dev/null | wc -l)"
echo "go_mod=$(head -3 /app/go.mod 2>/dev/null | tr '\n' ' ')"
'''


def probe(image, platform):
    subprocess.run(["docker", "pull", "-q", "--platform", platform, image], capture_output=True)
    p = subprocess.run(["docker", "run", "--rm", "--platform", platform, "--entrypoint", "bash", image, "-c", PROBE],
                       capture_output=True, text=True, timeout=600)
    info = {}
    for ln in p.stdout.splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            info[k] = v.strip()
    info["_rc"] = p.returncode
    if p.returncode != 0:
        info["_err"] = p.stderr[-500:]
    return info


def main():
    mode = sys.argv[1]
    plan = load_plan(set(), set())
    if mode == "compare":
        a = json.load(open(OUT / "official.json"))
        b = json.load(open(OUT / "mine.json"))
        for base in sorted(set(a) & set(b)):
            diffs = {k: (a[base].get(k), b[base].get(k)) for k in a[base]
                     if not k.startswith("_") and k != "arch" and a[base].get(k) != b[base].get(k)}
            if diffs:
                print("==", base)
                for k, (x, y) in diffs.items():
                    print(f"   {k}: official={x!r}  mine={y!r}")
        print("compared", len(set(a) & set(b)), "bases; missing in mine:", len(set(a) - set(b)))
        return
    ns, platform, outfile = (("jefzda/sweap-images", "linux/amd64", OUT / "official.json") if mode == "official"
                             else ("bdqnghi/sweap-images", "linux/arm64", OUT / "mine.json"))
    result = json.load(open(outfile)) if outfile.exists() else {}
    todo = [(local_name(b), rows[0]["dockerhub_tag"]) for b, rows in plan.items() if local_name(b) not in result]
    print(f"{mode}: {len(todo)} bases to probe", flush=True)

    def one(item):
        base, tag = item
        img = f"{ns}:{tag}"
        info = probe(img, platform)
        subprocess.run(["docker", "rmi", img], capture_output=True)
        print(f"{base[:60]:60s} os={info.get('os')} py={info.get('python')} go={info.get('go')} node={info.get('node')}", flush=True)
        return base, info

    with ThreadPoolExecutor(max_workers=3) as ex:
        for base, info in ex.map(one, todo):
            result[base] = info
            json.dump(result, open(outfile, "w"), indent=1)


if __name__ == "__main__":
    main()
