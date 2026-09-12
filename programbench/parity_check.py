#!/usr/bin/env python3
"""Compare toolchain/package inventories between official (amd64) and rebuilt (arm64) ProgramBench images.

For each instance: pull the official image, run the same probe in both, diff after normalising arch strings,
remove the official image. Usage: parity_check.py <instance_id> [...]  -> parity/<id>.{official,arm64,diff}.txt
"""
import difflib, json, re, subprocess, sys, time
from pathlib import Path

PROBE = r"""
export PATH=$PATH:/usr/local/go/bin:/root/go/bin:/root/.cargo/bin
echo "go: $(go version 2>/dev/null)"
echo "rustc: $(rustc --version 2>/dev/null)"; echo "cargo: $(cargo --version 2>/dev/null)"
echo "gcc: $(gcc --version 2>/dev/null | head -1)"; echo "g++: $(g++ --version 2>/dev/null | head -1)"
echo "clang: $(clang --version 2>/dev/null | head -1)"; echo "cmake: $(cmake --version 2>/dev/null | head -1)"
echo "make: $(make --version 2>/dev/null | head -1)"; echo "ninja: $(ninja --version 2>/dev/null)"
echo "meson: $(meson --version 2>/dev/null)"; echo "python: $(python3 --version 2>/dev/null)"
echo "node: $(node --version 2>/dev/null)"; echo "java: $(java -version 2>&1 | head -1)"
echo "zig: $(zig version 2>/dev/null)"; echo "pkg-config: $(pkg-config --version 2>/dev/null)"
echo "--- pip"; pip3 freeze 2>/dev/null | sort
echo "--- cargo registry"; ls /root/.cargo/registry/cache/*/ 2>/dev/null | sort
echo "--- go modcache"; find /usr/local/go/mod-cache/cache/download -name '*.zip' 2>/dev/null | sed 's#.*/download/##' | sort
echo "--- apt"; dpkg-query -W -f='${Package} ${Version}\n' 2>/dev/null | sort
echo "--- workspace"; ls -A /workspace 2>/dev/null | sort
"""
ARCH = [(r"x86_64|amd64", "ARCH"), (r"aarch64|arm64", "ARCH"), (r"linux-gnu-\w+", "linux-gnu")]


def norm(text):
    out = []
    for line in text.splitlines():
        for pat, rep in ARCH:
            line = re.sub(pat, rep, line)
        out.append(line)
    return out


def probe(image, platform=None):
    cmd = ["docker", "run", "--rm"] + (["--platform", platform] if platform else []) + ["--entrypoint", "bash", image, "-c", PROBE]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=1800).stdout


ARCH_ONLY_APT = {"libquadmath0", "libhwasan0"}  # exist for one architecture only


def sections(text):
    cur, out = "toolchain", {"toolchain": []}
    for line in text.splitlines():
        if line.startswith("--- "):
            cur = line[4:]; out[cur] = []
            continue
        for pat, rep in ARCH:
            line = re.sub(pat, rep, line)
        out[cur].append(line)
    return out


def missing(a, b):
    """What the official image has that ours lacks (apt/pip package names, toolchains), ignoring arch-only packages."""
    sa, sb = sections(a), sections(b)
    apt_a = {l.split()[0] for l in sa.get("apt", []) if l.strip()}
    apt_b = {l.split()[0] for l in sb.get("apt", []) if l.strip()}
    pip_a = {l.split("==")[0].lower() for l in sa.get("pip", []) if "==" in l}
    pip_b = {l.split("==")[0].lower() for l in sb.get("pip", []) if "==" in l}
    tool = [l for l in sa["toolchain"] if l not in sb["toolchain"] and not l.endswith(": ")]
    return {"apt": sorted(p for p in apt_a - apt_b if "ARCH" not in p and p not in ARCH_ONLY_APT),
            "pip": sorted(pip_a - pip_b), "toolchain": tool,
            "workspace": sorted(set(sa.get("workspace", [])) - set(sb.get("workspace", [])))}


def pull(ref):
    for _ in range(12):
        p = subprocess.run(["docker", "pull", "-q", "--platform", "linux/amd64", ref], capture_output=True, text=True)
        if p.returncode == 0:
            return True
        if "toomanyrequests" in p.stdout + p.stderr:
            print("[hub] rate limited; waiting 5 min", flush=True); time.sleep(300); continue
        return False
    return False


def check(inst):
    repo = inst.replace("__", "_1776_")
    off, ours = f"programbench/{repo}:task_cleanroom_v6", f"bdqnghi/{repo}:task_cleanroom_v6"
    if not pull(off):
        print(inst, "PULL FAILED", flush=True); return
    try:
        a, b = probe(off, "linux/amd64"), probe(ours)
    finally:
        subprocess.run(["docker", "rmi", off], capture_output=True)
    Path(f"parity/{inst}.official.txt").write_text(a); Path(f"parity/{inst}.arm64.txt").write_text(b)
    diff = list(difflib.unified_diff(norm(a), norm(b), "official", "arm64", lineterm="", n=0))
    Path(f"parity/{inst}.diff.txt").write_text("\n".join(diff) + "\n")
    m = missing(a, b)
    Path(f"parity/{inst}.missing.json").write_text(json.dumps(m, indent=1))
    flag = " MISSING" if any(m.values()) else ""
    print(f"{inst}: {len([l for l in diff if l[:1] in '+-' and not l.startswith(('+++', '---'))])} differing lines{flag} {json.dumps(m) if flag else ''}", flush=True)


def main():
    Path("parity").mkdir(exist_ok=True)
    args = sys.argv[1:]
    jobs = 2
    if args and args[0] == "--all":
        args = json.load(open("instances.json"))
    todo = [i for i in args if not Path(f"parity/{i}.missing.json").exists()]
    print(f"{len(todo)} instances to check", flush=True)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(jobs) as ex:
        list(ex.map(check, todo))


if __name__ == "__main__":
    main()
