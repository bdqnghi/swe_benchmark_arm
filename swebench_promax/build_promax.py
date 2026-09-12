#!/usr/bin/env python3
"""Rebuild SWE-Bench-ProMax task images natively for arm64 and push them to Docker Hub.

Official images: key4127/refactor-dockerhub:<instance_id> (amd64 only, no Dockerfiles published). Each image's
BuildKit history carries the full recipe on top of an official library image (ubuntu / python / golang / node /
rust / eclipse-temurin ...). We detect that base from the image and reconstruct the remaining steps
(tools/reconstruct.py), then build natively and push as bdqnghi/swebench-promax:<instance_id>. The eval runner
(test_run.py) takes the image from `image_name` in swe-bench-promax.json, so a rewritten copy of that file is
written to swe-bench-promax.arm64.json.
"""
import argparse, contextlib, json, os, re, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RECON = ROOT.parent / "tools" / "reconstruct.py"
lock = threading.Lock()
sys.path.insert(0, str(ROOT))
import test_run  # the official ProMax eval runner; reused for gold-patch validation
test_run.print = lambda *a, **k: None  # silence its per-command chatter (redirect_stdout would hijack all threads)

DATA = {d["instance_id"]: d for d in json.load(open(ROOT / "swe-bench-promax.json"))}
EVAL = json.load(open(ROOT / "eval.json"))


def validate_gold(inst, image):
    """Run the official eval script with the golden patch against our image (offline, like test_run.py)."""
    entry = DATA[inst]
    script = EVAL.get(inst, {}).get("eval_script", "")
    name = f"promax-val-{re.sub(r'[^A-Za-z0-9_.-]', '-', inst)}-{os.getpid()}"
    info, _, _, _ = test_run.run_single_patch(name, image, entry.get("patch"), script,
                                              entry.get("language", ""), entry.get("repo", ""))
    (ROOT / "validation").mkdir(exist_ok=True)
    json.dump(info, open(ROOT / "validation" / f"{inst}.json", "w"), indent=1, ensure_ascii=False)
    return bool(info.get("is_passed")), info.get("reason")


def log(msg):
    with lock:
        print(time.strftime("%H:%M:%S"), msg, flush=True)


def run(cmd, logfile, timeout=None):
    with open(logfile, "ab") as f:
        f.write(f"\n$ {' '.join(cmd)}\n".encode())
        f.flush()
        return subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=timeout).returncode


NET_ERRORS = ("curl 56", "GnuTLS recv error", "Could not resolve host", "Temporary failure in name resolution", "i/o timeout",
              "TLS handshake timeout", "incomplete-download", "Read timed out", "Connection broken", "connection reset by peer",
              "unexpected disconnect while reading sideband packet", "failed to resolve source metadata", "server misbehaving",
              "Failed to fetch", "Unable to fetch some archives", "early EOF", "read tcp ", "dial tcp")
TRANSIENT = ("toomanyrequests", "lookup registry-1.docker.io", "i/o timeout", "server misbehaving", "connection reset",
             "TLS handshake timeout", "EOF", "connection refused", "no such host", "temporary failure")


def pull(ref, logfile):
    for attempt in range(40):
        p = subprocess.run(["docker", "pull", "-q", "--platform", "linux/amd64", ref], capture_output=True, text=True)
        if p.returncode == 0:
            return True
        err = p.stdout + p.stderr
        if any(t in err for t in TRANSIENT):  # Hub rate limit or the host's flaky DNS: wait it out, never record
            log(f"[hub] transient pull error for {ref} ({err.strip().splitlines()[-1][:80]}); waiting 2 min")
            time.sleep(120)
            continue
        with open(logfile, "a") as f:
            f.write(err)
        return False
    return False


def sh(ref, cmd):
    return subprocess.run(["docker", "run", "--rm", "--platform", "linux/amd64", "--entrypoint", "sh", ref, "-c", cmd],
                          capture_output=True, text=True).stdout


BUILD_BACKENDS = {'calver', 'cmake', 'cython', 'editables', 'expandvars', 'flit-core', 'hatch-fancy-pypi-readme', 'hatch-vcs', 'hatchling', 'maturin', 'meson', 'meson-python', 'ninja', 'packaging', 'pathspec', 'pdm-backend', 'pip', 'poetry-core', 'pybind11', 'scikit-build-core', 'setuptools', 'setuptools-rust', 'setuptools-scm', 'trove-classifiers', 'versioneer', 'wheel'}

INST_HOLDER = "@@INST@@"

SNAPSHOT_PIPS = r"""
for d in $(find / -xdev -maxdepth 7 -name '*.dist-info' -not -path '*/node_modules/*' 2>/dev/null); do
  if [ -f "$d/direct_url.json" ] && grep -q '"editable": *true' "$d/direct_url.json"; then continue; fi
  basename "$d"
done
"""
SNAPSHOT_LOCKS = r"""
cd / && find testbed \( -name Cargo.lock -o -name package-lock.json -o -name yarn.lock -o -name pnpm-lock.yaml \
  -o -name poetry.lock -o -name uv.lock -o -name go.sum -o -name Gemfile.lock -o -name composer.lock \) \
  -not -path '*/node_modules/*' -not -path '*/target/*' -not -path '*/.git/*' -not -path '*/subprojects/*' \
  -not -path '*/build/*' -not -path '*/dist/*' -not -path '*/_deps/*' 2>/dev/null | tar cf - -T - 2>/dev/null
"""


# architecture-neutral caches the offline eval needs that no recipe step recreates (the official images were
# evidently warmed after their last Dockerfile step): deno module cache (dspy's pyodide runner), HF hub models.
SNAPSHOT_CACHES = r"""
cd / && ls -d root/.cache/deno root/.cache/huggingface root/.cargo/registry usr/local/cargo/registry \
  root/go/pkg/mod/cache/download usr/local/go/mod-cache/cache/download root/.m2/repository \
  root/.gradle/caches/modules-2 root/.npm/_cacache usr/local/bin/docker-entrypoint.sh root/.cache/bazel/_bazel_root/cache/repos 2>/dev/null \
  | tar cf - --exclude='v8_code_cache_v2*' --exclude='registry/src' -T - 2>/dev/null
"""


def snapshot(src, wd):
    """Record what the official image resolved: installed Python dists (-> pip/uv constraints) and lockfiles under /testbed."""
    wd.mkdir(parents=True, exist_ok=True)
    out = sh(src, SNAPSHOT_PIPS)
    cons = []
    for line in out.split():
        m = re.match(r"(.+?)-([^-]+)\.dist-info$", line)
        if not m:
            continue
        name, ver = m.group(1), m.group(2).split("+")[0]
        if name.lower().replace("_", "-") in BUILD_BACKENDS:
            continue  # build-isolation deps must stay free (pip applies constraints inside build envs too)
        cons.append(f"{name}=={ver}")
    (wd / "constraints.txt").write_text("\n".join(sorted(set(cons))) + "\n")
    with open(wd / "locks.tar", "wb") as f:
        subprocess.run(["docker", "run", "--rm", "--platform", "linux/amd64", "--entrypoint", "sh", src, "-c", SNAPSHOT_LOCKS],
                       stdout=f, stderr=subprocess.DEVNULL)
    if (wd / "locks.tar").stat().st_size < 1024:
        (wd / "locks.tar").unlink()
    wh = sh(src, f"ls /opt/promax-wheelhouse/{INST_HOLDER} 2>/dev/null".replace(INST_HOLDER, wd.name))
    pins = []
    for fn in wh.split():
        m = re.match(r"([A-Za-z0-9_.]+)-([0-9][^-]*)-(?:py|cp)", fn) or re.match(r"([A-Za-z0-9_.]+)-([0-9][^-]*)\.tar\.gz$", fn)
        if m:
            pins.append(f"{m.group(1).replace('_', '-')}=={m.group(2)}")
    if pins:  # the eval script installs offline from this directory; rebuild it with aarch64 wheels
        (wd / "wheelhouse.txt").write_text("\n".join(sorted(set(pins))) + "\n")
    with open(wd / "caches.tar", "wb") as f:
        subprocess.run(["docker", "run", "--rm", "--platform", "linux/amd64", "--entrypoint", "sh", src, "-c", SNAPSHOT_CACHES],
                       stdout=f, stderr=subprocess.DEVNULL)
    if (wd / "caches.tar").stat().st_size < 1024:
        (wd / "caches.tar").unlink()
    return len(cons), (wd / "locks.tar").exists()


# per-repository arm64 fixes inserted right after FROM (before the recipe runs)
REPO_PRE_FIXES = {
    # arm64 apt.llvm.org snapshot of llvm-18 is older than Ubuntu's libllvm18 -> mixed sources break; take everything from apt.llvm.org
    # (apt.llvm.org's arm64 snapshot lacks parts of the set, e.g. libpolly-18-dev, so Ubuntu's libllvm18 gets mixed in and Breaks it)
    "WasmEdge/WasmEdge": ['RUN printf "Package: *\\nPin: origin apt.llvm.org\\nPin-Priority: -1\\n" > /etc/apt/preferences.d/99-apt-llvm-org',
                          # aarch64 char is unsigned: `char == -1` trips -Werror=tautological-constant-out-of-range-compare in vfs_io.h;
                          # the recipe configures cmake, so the flag must be in place before it runs
                          "ENV CXXFLAGS=-fsigned-char CFLAGS=-fsigned-char"],
}

# per-repository arm64 fixes appended after the recipe (repo -> extra Dockerfile lines)
REPO_FIXES = {
    # aarch64 `char` is unsigned; ETL's tests narrow negative literals into char and only compile with x86's signed char
    "ETLCPP/etl": ['ENV CXXFLAGS=-fsigned-char CFLAGS=-fsigned-char'],
    # the test classpath only carries netty-tcnative's linux-x86_64 BoringSSL jar; Netty's loader also accepts the native
    # from java.library.path, so install the matching linux-aarch_64 build (version from the repo's MODULE.bazel)
    "bazelbuild/bazel": ['RUN set -eux; V=$(grep -oE "netty-tcnative-boringssl-static:jar:linux-aarch_64:[0-9A-Za-z.]+" /testbed/MODULE.bazel | head -1 | awk -F: \'{print $NF}\'); '
                         '[ -n "$V" ] || V=$(grep -oE "netty-tcnative-boringssl-static:jar:linux-x86_64:[0-9A-Za-z.]+" /testbed/MODULE.bazel | head -1 | awk -F: \'{print $NF}\'); '
                         'curl -fsSL -o /tmp/tcn.jar "https://repo1.maven.org/maven2/io/netty/netty-tcnative-boringssl-static/$V/netty-tcnative-boringssl-static-$V-linux-aarch_64.jar" '
                         '&& cd /tmp && unzip -o -j tcn.jar "META-INF/native/libnetty_tcnative_linux_aarch_64.so" -d /usr/lib/aarch64-linux-gnu/ && rm -f /tmp/tcn.jar '
                         '&& ls -la /usr/lib/aarch64-linux-gnu/libnetty_tcnative_linux_aarch_64.so'],
    # its eval script puts /usr/lib/x86_64-linux-gnu/pkgconfig on PKG_CONFIG_PATH
    "bloomberg/blazingmq": ['RUN mkdir -p /usr/lib/x86_64-linux-gnu && ln -sfn /usr/lib/aarch64-linux-gnu/pkgconfig /usr/lib/x86_64-linux-gnu/pkgconfig'],
    # mk/tools.mk errors at parse time for any host but linux-x86_64/macosx/windows, even for host unit tests
    "betaflight/betaflight": [
        r"""RUN cd /testbed && sed -i 's#^else ifeq ($(OSFAMILY), windows)#else ifeq ($(OSFAMILY)-$(ARCHFAMILY), linux-aarch64)\n  ARM_SDK_URL := https://developer.arm.com/-/media/Files/downloads/gnu/13.3.rel1/binrel/arm-gnu-toolchain-13.3.rel1-aarch64-arm-none-eabi.tar.xz\n  DL_CHECKSUM = 0\nelse ifeq ($(OSFAMILY), windows)#' mk/tools.mk && grep -q linux-aarch64 mk/tools.mk""",
    ],
}

# literal x86 paths baked into recipes that have a direct aarch64 counterpart
ARCH_REPLACEMENTS = {"openjdk-amd64": "openjdk-arm64", "GOARCH=amd64": "GOARCH=arm64",
                     ".linux-amd64.tar.gz": ".linux-arm64.tar.gz",  # go.dev toolchain tarballs
                     "TARGET_ARCH=x86_64": "TARGET_ARCH=arm64",  # istio build convention
                     "bazelisk-linux-amd64": "bazelisk-linux-arm64"}

# pure-python fallback wheels on aarch64 that need a system library the x86_64 wheel bundles
ARM64_APT_FIXES = {"soundfile": ["libsndfile1"]}


def apply_pins(wd, repo=""):
    """Inject the snapshot into the reconstructed Dockerfile: constraints right after FROM, lockfiles right after the clone."""
    df = wd / "Dockerfile"
    text = df.read_text()
    for old, new in ARCH_REPLACEMENTS.items():
        text = text.replace(old, new)
    lines = text.splitlines()
    out = []
    locks_done = not (wd / "locks.tar").exists()
    for i, line in enumerate(lines):
        out.append(line)
        if line.startswith("FROM "):
            out += ["COPY constraints.txt /opt/promax-constraints.txt",
                    "ENV PIP_CONSTRAINT=/opt/promax-constraints.txt UV_CONSTRAINT=/opt/promax-constraints.txt"]
            if re.search(r"(bullseye|buster)", line):
                # EOL Debian releases: deb.debian.org's security index still lists packages the pool no longer serves (404)
                out.append("RUN set -eux; for f in /etc/apt/sources.list /etc/apt/sources.list.d/*; do [ -f \"$f\" ] || continue; "
                           "sed -i -E '/debian-security/d; s#https?://deb\\.debian\\.org/debian#http://archive.debian.org/debian#g' \"$f\"; done; "
                           "echo 'Acquire::Check-Valid-Until \"false\";' > /etc/apt/apt.conf.d/99no-check-valid-until")
            out += REPO_PRE_FIXES.get(repo, [])
        if not locks_done and line.startswith("RUN") and re.search(r"git clone|git reset --hard|git checkout", line):
            out.append("ADD locks.tar /")
            locks_done = True
    cons = {l.split("==")[0].lower().replace("_", "-") for l in (wd / "constraints.txt").read_text().split()}
    pkgs = sorted({p for name, ps in ARM64_APT_FIXES.items() if name in cons for p in ps})
    if pkgs:  # appended last so the (slow) earlier layers stay cacheable
        out.append("RUN apt-get update && apt-get install -y --no-install-recommends " + " ".join(pkgs) + " && rm -rf /var/lib/apt/lists/*")
    if (wd / "caches.tar").exists():
        out.append("ADD caches.tar /")
    if (wd / "wheelhouse.txt").exists():
        out += ["COPY wheelhouse.txt /opt/promax-wheelhouse.txt",
                # one download per pin: the baked wheelhouse may hold several versions of the same package
                f"RUN mkdir -p /opt/promax-wheelhouse/{wd.name} && while read -r req; do [ -n \"$req\" ] || continue; "
                f"env -u PIP_CONSTRAINT -u UV_CONSTRAINT pip download --no-deps --prefer-binary -d /opt/promax-wheelhouse/{wd.name} \"$req\"; "
                f"done < /opt/promax-wheelhouse.txt"]
    # eval scripts may hard-code the Debian/Ubuntu JVM path for amd64; alias it to the arm64 JVM(s) in the image
    out.append("RUN for d in /usr/lib/jvm/java-*-openjdk-arm64; do [ -d \"$d\" ] && ln -sfn \"$d\" \"${d%-arm64}-amd64\"; done; true")
    out += REPO_FIXES.get(repo, [])
    df.write_text("\n".join(out) + "\n")


def detect_base(ref):
    """Return (FROM, skip_until_regex) for the official library image the task image was built on."""
    hist = subprocess.run(["docker", "history", "--no-trunc", "--format", "{{.CreatedBy}}", ref],
                          capture_output=True, text=True).stdout
    osr = sh(ref, "cat /etc/os-release")
    codename = (re.search(r"VERSION_CODENAME=(\w+)", osr) or re.search(r"\(([a-z]+)\)", osr))
    codename = codename.group(1) if codename else ""
    vid = re.search(r'VERSION_ID="?([\d.]+)', osr)
    env = dict(re.findall(r"(?:ENV|ARG) (\w+)=(\S+)", hist))
    labels = json.loads(subprocess.run(["docker", "inspect", "--format", "{{json .Config.Labels}}", ref],
                                       capture_output=True, text=True).stdout or "null") or {}
    if labels.get("io.istio.repo") == "https://github.com/istio/tools" and labels.get("io.istio.version"):
        # istio build-tools image (multi-arch on gcr.io); its rootfs arrives via COPY layers history cannot replay.
        # The task author's own steps start at `ENV TZ=Etc/UTC`.
        return f"gcr.io/istio-testing/build-tools:{labels['io.istio.version']}", r"^ENV TZ=Etc/UTC$"
    if "JAVA_VERSION" in env and 'ENTRYPOINT ["/usr/local/bin/mvn-entrypoint.sh"]' in hist and "MAVEN_VERSION" in env:
        # official maven image (its /usr/share/maven arrives via COPY, which history cannot reproduce)
        major = re.sub(r"^jdk-?", "", env["JAVA_VERSION"]).split(".")[0]
        return f"maven:{env['MAVEN_VERSION']}-eclipse-temurin-{major}-{codename}", r'^CMD \["mvn"\]'
    if "PYTHON_VERSION" in env:
        return f"python:{env['PYTHON_VERSION']}-{codename}", r'^CMD \["python3"\]'
    if "GOLANG_VERSION" in env:
        return f"golang:{env['GOLANG_VERSION']}-{codename}", r"^WORKDIR /go$"
    if "NODE_VERSION" in env:
        return f"node:{env['NODE_VERSION']}-{codename}", r'^CMD \["node"\]'
    if "RUST_VERSION" in env:
        return f"rust:{env['RUST_VERSION']}-{codename}", r"^RUN set -eux;.*rustup"
    if "JAVA_VERSION" in env:  # JAVA_VERSION=jdk-17.0.17+10 -> eclipse-temurin:17.0.17_10-jdk-noble
        jv = re.sub(r"^jdk-?", "", env["JAVA_VERSION"]).replace("+", "_")
        return f"eclipse-temurin:{jv}-jdk-{codename}", r'^CMD \["jshell"\]'
    if "Ubuntu" in osr and vid:
        return f"ubuntu:{vid.group(1)}", ""
    if "Debian" in osr and codename:
        return f"debian:{codename}", ""
    return None, ""


def build_instance(inst, args, status):
    src = f"key4127/refactor-dockerhub:{inst}"
    dst = f"{args.namespace}:{inst}"
    prev = status.data.get(inst) or {}
    if args.validate_only:
        gold, reason = validate_gold(inst, dst)
        log(f"{inst}: gold {'PASS' if gold else 'FAIL (' + str(reason) + ')'}")
        status.set(inst, **{**prev, "gold": gold})
        return gold
    if not args.force and prev.get("ok") and (prev.get("pushed") or not args.push) and prev.get("gold") is not False:
        log(f"{inst}: done previously")
        return True
    wd = ROOT / "work" / inst
    logfile = ROOT / "logs" / f"{inst}.log"
    logfile.parent.mkdir(exist_ok=True)
    logfile.write_text("")
    t0 = time.time()
    if not (wd / "Dockerfile").exists() or args.force:
        log(f"{inst}: pulling official image")
        if not pull(src, logfile):
            log(f"{inst}: FAILED (pull)")
            status.set(inst, ok=False, error="pull")
            return False
        base, skip_until = detect_base(src)
        if not base:
            log(f"{inst}: FAILED (unknown base)")
            status.set(inst, ok=False, error="base")
            return False
        cmd = [sys.executable, str(RECON), src, str(wd), "--from", base]
        if skip_until:
            cmd += ["--skip-until", skip_until]
        rc = run(cmd, logfile)
        npins = locks = None
        if rc == 0:
            npins, locks = snapshot(src, wd)
            apply_pins(wd, DATA[inst].get("repo", ""))
        subprocess.run(["docker", "rmi", src], capture_output=True)
        if rc != 0:
            log(f"{inst}: FAILED (reconstruct)")
            status.set(inst, ok=False, error="reconstruct")
            return False
        log(f"{inst}: base {base}, {npins} python pins, lockfiles={'yes' if locks else 'no'}")
    log(f"{inst}: building")
    for attempt in range(15):
        rc = run(["docker", "build", "--platform", "linux/arm64", "--progress=plain", "-t", dst, str(wd)], logfile, timeout=args.timeout)
        if rc == 0:
            break
        logtext = (wd.parent.parent / "logs" / f"{inst}.log").read_text(errors="ignore")
        tail = logtext[-20000:]
        if any(t in tail for t in NET_ERRORS):  # the host's DNS/network hiccups: wait and rebuild (cached layers make it cheap)
            log(f"{inst}: build hit a network error; retrying in 2 min")
            time.sleep(120)
            continue
        # pip: a snapshot constraint contradicts the repo's own exact pin (the official image evidently installed something
        # else later). Drop the offending constraints and retry rather than fail the whole instance.
        clash = set(re.findall(r"The user requested \(constraint\) ([A-Za-z0-9_.-]+)==", logtext))
        cons_path = wd / "constraints.txt"
        if not clash or not cons_path.exists():
            break
        norm = {c.lower().replace("_", "-") for c in clash}
        prefixes = {c.rsplit("-", 1)[0] + "-" for c in norm if c.count("-") >= 2}  # e.g. fprime-fpp-check -> fprime-fpp-*
        keep = [l for l in cons_path.read_text().splitlines()
                if l.split("==")[0].lower().replace("_", "-") not in norm
                and not any(l.split("==")[0].lower().replace("_", "-").startswith(p) for p in prefixes)]
        if len(keep) == len(cons_path.read_text().splitlines()):
            break
        cons_path.write_text("\n".join(keep) + "\n")
        log(f"{inst}: dropping conflicting constraints {sorted(clash)} and retrying build")
    dur = round(time.time() - t0)
    if rc != 0:
        log(f"{inst}: FAILED ({dur}s) see {logfile}")
        status.set(inst, ok=False, seconds=dur, error="build")
        return False
    gold = None
    if not args.no_validate:
        log(f"{inst}: validating gold patch")
        gold, reason = validate_gold(inst, dst)
        log(f"{inst}: gold {'PASS' if gold else 'FAIL (' + str(reason) + ')'}")
    pushed = True
    if args.push:
        for attempt in range(20):
            pushed = run(["docker", "push", dst], logfile) == 0
            if pushed:
                break
            log(f"{inst}: push failed; retrying in 2 min")
            time.sleep(120)
    log(f"{inst}: built ({dur}s){', pushed' if args.push and pushed else (', PUSH FAILED' if args.push else '')}")
    if args.push and pushed and not args.keep:
        subprocess.run(["docker", "rmi", dst], capture_output=True)
    status.set(inst, ok=True, pushed=pushed, seconds=dur, gold=gold)
    return pushed and gold is not False


class Status:
    def __init__(self, path):
        self.path = path
        self.data = json.load(open(path)) if path.exists() else {}

    def set(self, key, **kw):
        with lock:
            self.data[key] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **kw}
            tmp = self.path.with_suffix(".tmp")
            json.dump(self.data, open(tmp, "w"), indent=1)
            os.replace(tmp, self.path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", action="append")
    ap.add_argument("--namespace", default="bdqnghi/swebench-promax")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--timeout", type=int, default=3 * 3600)
    ap.add_argument("--no-validate", action="store_true", help="skip the golden-patch eval after building")
    ap.add_argument("--validate-only", action="store_true", help="only run the golden-patch eval (image must exist or be pullable)")
    args = ap.parse_args()
    data = list(DATA.values())
    instances = [d["instance_id"] for d in data]
    if args.instance:
        instances = [i for i in instances if i in set(args.instance)]
    # dataset copy pointing at our images
    for d in data:
        d["image_name"] = f"{args.namespace}:{d['instance_id']}"
    json.dump(data, open(ROOT / "swe-bench-promax.arm64.json", "w"), ensure_ascii=False, indent=1)
    log(f"plan: {len(instances)} instances -> {args.namespace}:<id> (push={args.push})")
    status = Status(ROOT / "status.json")
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(build_instance, i, args, status): i for i in instances}
        for f in as_completed(futs):
            try:
                good = f.result()
            except Exception as e:
                log(f"{futs[f]}: EXCEPTION {e}")
                status.set(futs[f], ok=False, error=str(e))
                good = False
            ok += good
            fail += not good
    log(f"DONE ok={ok} fail={fail}")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
