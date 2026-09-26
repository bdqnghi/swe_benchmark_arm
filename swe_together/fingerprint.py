#!/usr/bin/env python3
"""Record what the official amd64 task image resolved (toolchains, Python distributions, global npm packages,
dpkg), so the arm64 rebuild can be pinned to it (drift control) and compared afterwards (parity).

Runs a probe script in the image (under qemu for linux/amd64). Output: fingerprints/<task>.json.
Official images pulled for this are removed again afterwards unless --keep (they were not present before).

  python -u fingerprint.py --jobs 4                     # all canonical tasks without a fingerprint
  python -u fingerprint.py --task X --image bdqnghi/swe-together:X --platform linux/arm64 --out parity/X.json
"""
import argparse, json, subprocess, sys, threading, time, tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
SWT = Path("/home/nghibui/codes/tib/third_party/swe-together")
lock = threading.Lock()

PROBE = r'''
export PATH="/usr/local/go/bin:/root/go/bin:/usr/local/cargo/bin:/root/.cargo/bin:/home/agent/.cargo/bin:/root/.bun/bin:/home/agent/.bun/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
echo "@@tools"
for c in bun node npm pnpm yarn deno uv go rustc cargo python3 pip3 gcc cmake java; do
  p=$(command -v $c 2>/dev/null) || continue
  case $c in
    go) v=$(go version 2>&1 | head -1);;
    java) v=$(java -version 2>&1 | head -1);;
    *) v=$($c --version 2>&1 | head -1);;
  esac
  echo "$c	$p	$v"
done
echo "@@pythons"
{ for p in /usr/bin/python3 /usr/local/bin/python3 /opt/venv/bin/python /venv/bin/python /opt/conda/bin/python; do [ -x "$p" ] && echo "$p"; done
  find / -xdev -maxdepth 6 -path /proc -prune -o \( -name python -o -name python3 \) -path "*/bin/*" -print 2>/dev/null; } | while read -r p; do
  [ -x "$p" ] && echo "$p"; done | sort -u > /tmp/.pys
seen=""
for p in $(cat /tmp/.pys); do
  pre=$("$p" -c 'import sys;print(sys.prefix)' 2>/dev/null) || continue
  case " $seen " in *" $pre "*) continue;; esac
  seen="$seen $pre"
  echo "@@py $p"
  "$p" - <<'PY' 2>/dev/null
import sys, json
try:
    from importlib import metadata as md
except Exception:
    sys.exit(0)
out = []
for d in md.distributions():
    try:
        name = d.metadata["Name"]
    except Exception:
        continue
    du = None
    try:
        t = d.read_text("direct_url.json")
        du = json.loads(t) if t else None
    except Exception:
        pass
    out.append({"name": name, "version": d.version, "direct_url": du, "path": str(getattr(d, "_path", ""))})
print(json.dumps({"version": sys.version.split()[0], "prefix": sys.prefix, "dists": out}))
PY
done
echo "@@npmg"
command -v npm >/dev/null && npm ls -g --depth=0 --json 2>/dev/null | tr -d '\n'; echo
echo "@@dpkg"
command -v dpkg-query >/dev/null && dpkg-query -W -f '${Package}=${Version}\n' 2>/dev/null
echo "@@end"
'''


def log(msg):
    with lock:
        print(time.strftime("%H:%M:%S"), msg, flush=True)


def official_image(task):
    return tomllib.loads((SWT / "tasks" / task / "task.toml").read_text())["environment"]["docker_image"]


def parse(text):
    fp = {"tools": {}, "pythons": {}, "npm_global": {}, "dpkg": {}}
    sec, cur = None, None
    for line in text.splitlines():
        if line.startswith("@@"):
            sec = line[2:].split()[0]
            cur = line[5:].strip() if sec == "py" else None
            continue
        if sec == "tools" and "\t" in line:
            c, p, v = (line.split("\t") + ["", ""])[:3]
            fp["tools"][c] = {"path": p, "version": v}
        elif sec == "py" and line.startswith("{"):
            try:
                fp["pythons"][cur] = json.loads(line)
            except json.JSONDecodeError:
                pass
        elif sec == "npmg" and line.startswith("{"):
            try:
                fp["npm_global"] = {k: v.get("version") for k, v in json.loads(line).get("dependencies", {}).items()}
            except Exception:  # noqa: BLE001
                pass
        elif sec == "dpkg" and "=" in line:
            k, v = line.split("=", 1)
            fp["dpkg"][k] = v
    return fp


def fingerprint(image, platform, pull=True):
    if pull:
        for attempt in range(5):
            r = subprocess.run(["docker", "pull", "-q", "--platform", platform, image], capture_output=True, text=True)
            if r.returncode == 0:
                break
            time.sleep(60)
        else:
            raise RuntimeError("pull failed: " + r.stderr[-300:])
    r = subprocess.run(["docker", "run", "--rm", "-u", "root", "--platform", platform, "--network", "none",
                        "--entrypoint", "", image, "bash", "-c", PROBE],
                       capture_output=True, text=True, timeout=1800)
    fp = parse(r.stdout)
    fp["image"] = image
    fp["platform"] = platform
    return fp


def one(task, args):
    out = Path(args.out) if args.out else HERE / "fingerprints" / f"{task}.json"
    if out.exists() and not args.force:
        return
    image = args.image or official_image(task)
    platform = args.platform
    had = subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode == 0
    t0 = time.time()
    try:
        fp = fingerprint(image, platform, pull=not had)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(fp, indent=1, sort_keys=True))
        log(f"{task}: {len(fp['tools'])} tools, {sum(len(p.get('dists', [])) for p in fp['pythons'].values())} "
            f"dists ({round(time.time() - t0)}s)")
    except Exception as e:  # noqa: BLE001
        log(f"{task}: FAILED {e}")
    finally:
        if not had and not args.keep:
            subprocess.run(["docker", "rmi", image], capture_output=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", action="append")
    ap.add_argument("--image")
    ap.add_argument("--platform", default="linux/amd64")
    ap.add_argument("--out")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    tasks = args.task or json.load(open(SWT / "canonical_full109.json"))["tasks"]
    with ThreadPoolExecutor(args.jobs) as ex:
        list(ex.map(lambda t: one(t, args), tasks))


if __name__ == "__main__":
    main()
