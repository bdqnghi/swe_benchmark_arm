#!/usr/bin/env python3
"""Build SWE-bench Pro images natively for arm64 and push them to a Docker Hub namespace.

Layout expected next to this script:
  instances.json                       -- from the HF dataset (instance_id, repo, dockerhub_tag, ...)
  dockerfiles/base_dockerfile/<iid>/Dockerfile
  dockerfiles/instance_dockerfile/<iid>/Dockerfile

Outputs:
  work/base/<base_name>/Dockerfile     -- patched base Dockerfiles actually used
  logs/base/<base_name>.log, logs/instance/<iid>.log
  status.json                          -- per-image result, used to resume
"""
import argparse, collections, fcntl, json, os, re, shutil, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ECR_PREFIX = "084828598639.dkr.ecr.us-west-2.amazonaws.com/docker-hub/library/"
PLATFORM = "linux/arm64"

lock = threading.Lock()


def log(msg):
    with lock:
        print(time.strftime("%H:%M:%S"), msg, flush=True)


def run(cmd, logfile, timeout=None):
    with open(logfile, "ab") as f:
        f.write(f"\n$ {' '.join(cmd)}\n".encode())
        f.flush()
        p = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
    return p.returncode


# Per-base fixes for drift between when Scale built the official images and now. Each entry:
# (substring of the base name that must match, old text, new text). Applied to the base Dockerfile.
BASE_PATCHES = [
    # navidrome: official image has TagLib 2.0.2 built from then-master; master is now 2.3.x and needs -lz.
    # golang:1.24 has moved from bookworm to trixie; official image is bookworm (go1.24.3, python 3.11).
    ("base_navidrome__navidrome",
     "https://github.com/taglib/taglib/archive/refs/heads/master.tar.gz",
     "https://github.com/taglib/taglib/archive/refs/tags/v2.0.2.tar.gz"),
    ("base_navidrome__navidrome", "cd /tmp/taglib-master", "cd /tmp/taglib-2.0.2"),
]


FINGERPRINTS = ROOT / "fingerprints/official.json"
_official = json.load(open(FINGERPRINTS)) if FINGERPRINTS.exists() else {}
DEBIAN_CODENAMES = {"10": "buster", "11": "bullseye", "12": "bookworm", "13": "trixie"}
SNAPSHOT = "20250801T000000Z"   # Debian snapshot date for EOL suites (around when the official images were built)
APT_FIX = (
    "# arm64 rebuild: bullseye is EOL -> snapshot.debian.org (has security updates); buster is only on "
    "archive.debian.org; ignore expired Release files\n"
    "RUN if [ -d /etc/apt/apt.conf.d ]; then echo 'Acquire::Check-Valid-Until \"false\";' "
    "> /etc/apt/apt.conf.d/99no-check-valid-until; fi; "
    ". /etc/os-release 2>/dev/null; case \"$VERSION_CODENAME\" in "
    "bullseye) printf 'deb http://snapshot.debian.org/archive/debian/" + SNAPSHOT + " bullseye main\\n"
    "deb http://snapshot.debian.org/archive/debian-security/" + SNAPSHOT + " bullseye-security main\\n"
    "deb http://snapshot.debian.org/archive/debian/" + SNAPSHOT + " bullseye-updates main\\n' > /etc/apt/sources.list; "
    "rm -f /etc/apt/sources.list.d/*.sources /etc/apt/sources.list.d/*.list;; "
    "buster|stretch) printf 'deb http://archive.debian.org/debian %s main\\n"
    "deb http://archive.debian.org/debian-security %s/updates main\\n' \"$VERSION_CODENAME\" \"$VERSION_CODENAME\" > /etc/apt/sources.list; "
    "rm -f /etc/apt/sources.list.d/*.sources /etc/apt/sources.list.d/*.list;; esac\n"
    "# arm64 rebuild: GitHub no longer serves git:// (old submodule URLs); rewrite to https\n"
    "RUN printf '[url \"https://github.com/\"]\\n\\tinsteadOf = git://github.com/\\n' >> /root/.gitconfig\n"
)


PYQT5_CORE = "python3-pyqt5 python3-pyqt5.qtwebengine libqt5sql5-sqlite"
PYQT5_OPT = ("python3-pyqt5.sip python3-pyqt5.qtwebchannel python3-pyqt5.qtquick python3-pyqt5.qtopengl "
             "python3-pyqt5.qtsql python3-pyqt5.qtsvg python3-pyqt5.qtx11extras")
PYQT5_SOURCE_RUN = (
    "\n# arm64 patch: PyQt5 from source for python 3.8 (see build.py)\n"
    "RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends build-essential "
    "qtbase5-dev qtwebengine5-dev qtdeclarative5-dev libqt5svg5-dev libqt5x11extras5-dev qttools5-dev libqt5sql5-sqlite "
    "libqt5webchannel5-dev libqt5opengl5-dev libqt5positioning5 qtpositioning5-dev qt5-qmake libgl1-mesa-dev qtmultimedia5-dev "
    "libqt5sensors5-dev libqt5serialport5-dev libqt5websockets5-dev libqt5xmlpatterns5-dev libqt5texttospeech5-dev "
    "libqt5remoteobjects5-dev qtconnectivity5-dev && rm -rf /var/lib/apt/lists/* \\\n"
    "    && printf 'sip==6.8.3\\nPyQt-builder==1.16.4\\nsetuptools<75\\n' > /tmp/pyqt-constraints.txt \\\n"
    "    && PIP_CONSTRAINT=/tmp/pyqt-constraints.txt pip install --no-binary :all: --config-settings --confirm-license= "
    "--config-settings \"--jobs=$(nproc)\" PyQt5==5.15.10 \\\n"
    "    && PIP_CONSTRAINT=/tmp/pyqt-constraints.txt pip install --no-binary :all: --config-settings \"--jobs=$(nproc)\" PyQtWebEngine==5.15.7 \\\n"
    "    && QT_QPA_PLATFORM=offscreen python -c 'from PyQt5 import QtCore, QtWidgets, QtWebEngineWidgets, QtSql; "
    "print(\"PyQt5\", QtCore.PYQT_VERSION_STR, \"Qt\", QtCore.QT_VERSION_STR, QtSql.QSqlDatabase.drivers())'\n"
)
PYQT6_CORE = "python3-pyqt6 python3-pyqt6.qtwebengine libqt6sql6-sqlite"
PYQT6_OPT = "python3-pyqt6.sip python3-pyqt6.qtwebchannel python3-pyqt6.qtqml python3-pyqt6.qtquick python3-pyqt6.qtsvg"
PYQT_RUN = (
    "\n# arm64 patch: PyQt5 has no aarch64 wheels on PyPI; use the distro's PyQt packages and expose them to the\n"
    "# image's python (python:*-slim ships its own interpreter; the distro modules are ABI-compatible, same minor).\n"
    "# Optional module packages are installed one by one since names differ between Debian and Ubuntu releases.\n"
    "RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends {core} \\\n"
    "    && for p in {opt}; do DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $p || true; done "
    "&& rm -rf /var/lib/apt/lists/* \\\n"
    "    && SITE=$(python3 -c 'import site; print(site.getsitepackages()[0])') "
    "&& if [ \"$SITE\" != /usr/lib/python3/dist-packages ]; then "
    "for d in /usr/lib/python3/dist-packages/PyQt{major}* ; do ln -sfn \"$d\" \"$SITE/$(basename \"$d\")\"; done; fi \\\n"
    "    && QT_QPA_PLATFORM=offscreen python3 -c 'from PyQt{major} import QtCore, QtWidgets, QtWebEngineWidgets, QtSql; "
    "print(\"PyQt{major}\", QtCore.PYQT_VERSION_STR, \"Qt\", QtCore.QT_VERSION_STR, QtSql.QSqlDatabase.drivers())'\n"
)


def timemachine_date(iid):
    m = re.search(r"pypi-timemachine (\d{4}-\d{2}-\d{2})", (ROOT / "dockerfiles/instance_dockerfile" / iid / "Dockerfile").read_text())
    return m.group(1) if m else ""


def qutebrowser_mode(base, rows):
    """How PyQt gets into a qutebrowser image:
      ("distro", 5|6)  distro packages (no aarch64 PyQt5 wheels exist; PyQt6 aarch64 wheels only from 6.7.1, 2024-07)
      ("pypi", 6)      leave the Dockerfile alone: the pinned index date already resolves PyQt6 with aarch64 wheels
      ("pypi-pinned", 6) Ubuntu 22.04 has no python3-pyqt6 package: install the first aarch64 PyQt6 wheels from PyPI"""
    rows = GROUP_ROWS.get(base, rows)   # decide per group, never per instance: the base build must match
    major = qutebrowser_pyqt_major(rows)
    tm = min(timemachine_date(r["instance_id"]) for r in rows)
    fp_os = (_official.get(local_name(base)) or {}).get("os", "")
    # Newer PyQt6 aarch64 wheels need glibc 2.39 (manylinux_2_39); bookworm/jammy have 2.36/2.35, so the newest
    # usable wheels are the 6.7.x line (manylinux_2_28). Used where the official image got PyQt6 from PyPI
    # (date allows aarch64 wheels) or where the distro has no python3-pyqt6 (Ubuntu 22.04).
    if major == 6 and (tm >= "2024-08-01" or fp_os.startswith("Ubuntu 22.04")):
        return ("pypi-pinned", 6)
    return ("distro", major)


def qutebrowser_pyqt_major(rows):
    """6 if this base group's instances install PyQt6, else 5."""
    for r in rows:
        t = (ROOT / "dockerfiles/instance_dockerfile" / r["instance_id"] / "Dockerfile").read_text()
        if "PyQt6" in t:
            return 6
    return 5


def insert_before_repo_setup(text, snippet):
    idx = text.find("# REPO SETUP")
    if idx == -1:
        return text.replace("ENTRYPOINT", snippet + "ENTRYPOINT", 1)
    banner = text.rfind("\n####", 0, idx)
    return text[:banner + 1] + snippet + text[banner + 1:]


def official_codename(base):
    """Debian codename of the official image for this base (from fingerprints/official.json), or None."""
    os_name = (_official.get(local_name(base)) or {}).get("os", "")
    m = re.match(r"Debian GNU/Linux (\d+)", os_name)
    return DEBIAN_CODENAMES.get(m.group(1)) if m else None


MANIFEST_CACHE = ROOT / "tag_exists.json"
_manifest_cache = json.load(open(MANIFEST_CACHE)) if MANIFEST_CACHE.exists() else {}


def tag_exists(ref):
    """True if ref has an arm64 manifest on Docker Hub. Cached on disk (each lookup counts against the Hub
    pull rate limit: 200/h for a free account). On rate limiting, waits and retries instead of guessing."""
    if ref in _manifest_cache:
        return _manifest_cache[ref]
    # A pull costs the same one "pull" as a manifest check but leaves the image in the daemon store, so the
    # subsequent docker build resolves FROM locally instead of spending another pull.
    for attempt in range(12):
        p = subprocess.run(["docker", "pull", "-q", "--platform", PLATFORM, ref], capture_output=True, text=True)
        out = p.stdout + p.stderr
        if "toomanyrequests" in out or "rate limit" in out.lower():
            log(f"[hub] rate limited pulling {ref}; waiting 5 min (attempt {attempt + 1}/12)")
            time.sleep(300)
            continue
        if p.returncode == 0:
            exists = True
        elif "not found" in out.lower() or "manifest unknown" in out or "no matching manifest" in out:
            exists = False
        else:
            log(f"[hub] error checking {ref}: {out.strip()[-200:]}; retrying")
            time.sleep(30)
            continue
        with lock:
            _manifest_cache[ref] = exists
            json.dump(_manifest_cache, open(MANIFEST_CACHE, "w"), indent=1)
        return exists
    raise RuntimeError(f"could not check {ref} on Docker Hub after retries")


def exact_version(image, tag, fp):
    """Exact toolchain version the official image has, if the Dockerfile tag is a floating major/minor."""
    if image == "golang":
        m = re.match(r"go(\d+\.\d+\.\d+)$", fp.get("go", ""))
    elif image == "node":
        m = re.match(r"v(\d+\.\d+\.\d+)$", fp.get("node", ""))
    elif image == "python":
        m = re.match(r"Python (\d+\.\d+\.\d+)$", fp.get("python", ""))
    else:
        return None
    if not m:
        return None
    ver = m.group(1)
    floating = re.match(r"(\d+(?:\.\d+)?)(-|$)", tag)   # "1.24", "18", "3.11" but not "3.11.1"
    return ver if floating and ver.startswith(floating.group(1) + ".") else None


def pin_from(line, base):
    """Pin floating Docker Hub tags (python:3.11-slim, golang:1.24, node:18, ...) to the official image's
    Debian release, so we do not silently build on a newer distro than Scale did."""
    m = re.match(r"FROM\s+(python|golang|node):(\S+)(.*)$", line)
    if not m:
        return line
    image, tag, rest = m.groups()
    fp = _official.get(local_name(base)) or {}
    os_name = fp.get("os", "")
    exact = exact_version(image, tag, fp)
    if exact:
        # e.g. golang:1.24 -> golang:1.24.3, node:18 -> node:18.20.8, python:3.11-slim -> python:3.11.13-slim
        cand = re.sub(r"^\d+(?:\.\d+)?", exact, tag, count=1)
        cand_line = pin_from_codename(image, cand, rest, base, os_name)
        if tag_exists(cand_line.split()[1]):
            return cand_line
        log(f"[base] {base}: no arm64 tag {cand_line.split()[1]}; falling back to {tag}")
    return pin_from_codename(image, tag, rest, base, os_name)


def pin_from_codename(image, tag, rest, base, os_name):
    line = f"FROM {image}:{tag}{rest}"
    if tag.endswith("-alpine"):
        m2 = re.match(r"Alpine Linux v(\d+\.\d+)", os_name)
        return f"FROM {image}:{tag}{m2.group(1)}{rest}" if m2 else line
    if re.search(r"alpine|bookworm|bullseye|buster|trixie|windowsservercore", tag):
        return line
    codename = official_codename(base)
    if not codename:
        return line
    return f"FROM {image}:{tag}-{codename}{rest}"


PIP_UPGRADE_RE = re.compile(r"^(?P<pre>.*\bpip3? install\b[^\n]*?(?:--upgrade|-U)\b[^\n]*?)(?<=\s)pip(?=\s|\\|$)", re.M)


def pin_pip(text, base):
    """`pip install --upgrade pip` in a base Dockerfile runs against live PyPI, so it drifts from the official
    image (e.g. pip 26 rejects sdists that pip 25 accepted). Pin it to the official image's pip version."""
    ver = (_official.get(local_name(base)) or {}).get("pip", "")
    if not re.match(r"\d+(\.\d+)+$", ver):
        return text
    new, n = PIP_UPGRADE_RE.subn(lambda m: m.group("pre") + f"pip=={ver}", text)
    if n:
        log(f"[base] {base}: pinned pip=={ver} ({n} line(s))")
    return new


def pin_nodesource(text, base):
    """NodeSource's setup_lts.x/setup_NN.x install today's newest Node of that line (and its bundled npm); pin the
    exact version the official image has (NodeSource keeps every release of a major in its apt repo)."""
    if "deb.nodesource.com/setup_" not in text:
        return text
    m = re.match(r"v(\d+)\.(\d+)\.(\d+)$", (_official.get(local_name(base)) or {}).get("node", ""))
    if not m:
        return text
    major, ver = m.group(1), m.group(0)[1:]
    new = re.sub(r"deb\.nodesource\.com/setup_(?:lts|current|\d+)\.x", f"deb.nodesource.com/setup_{major}.x", text)
    new = re.sub(r"apt-get install -y nodejs\b(?![=\w-])",
                 f"(apt-get install -y nodejs={ver}-1nodesource1 || apt-get install -y nodejs)", new)
    if new != text:
        log(f"[base] {base}: pinned NodeSource node {ver}")
    return new


def patch_base(text, base="", rows=()):
    """Rewrite the base Dockerfile so it builds on arm64 and matches the official image's distro."""
    text = text.replace("FROM " + ECR_PREFIX, "FROM ")
    text = pin_pip(text, base)
    # some pinned commits are no longer on any branch of the upstream clone (still fetchable by SHA on GitHub)
    text = re.sub(r"^RUN git checkout ([0-9a-f]{40})\s*$",
                  r"RUN (git cat-file -e \1^{commit} 2>/dev/null || git fetch -q origin \1) && git checkout \1", text, flags=re.M)
    text = pin_nodesource(text, base)
    # `npm install -g yarn` now installs Yarn 4 (needs Node >= 18.12); the official images got classic 1.22.x,
    # which then runs `yarn set version` for the project's own berry release.
    text, n = re.subn(r"npm install -g yarn(?![@\w-])", "npm install -g yarn@1.22.22", text)
    if n:
        log(f"[base] {base}: pinned global yarn@1.22.22")
    # classic `yarn set version X` delegates to the newest berry bundle, which refuses Node < 18.12 unless told not to
    text = re.sub(r"(?<![\w=])yarn set version ", "YARN_IGNORE_NODE=1 yarn set version ", text)
    if "libxml2-dev" in text and "zlib1g-dev" not in text:
        # lxml has no aarch64 wheels for some pinned versions, so it builds from source and links -lz
        text = re.sub(r"(\s)libxml2-dev(\s)", r"\1libxml2-dev zlib1g-dev\2", text, count=1)
        log(f"[base] {base}: added zlib1g-dev next to libxml2-dev")
    if re.search(r"^FROM \S*alpine", text, re.M):
        # Go's linker passes -fuse-ld=gold for cgo external linking on linux/arm64; alpine images lack gold.
        text = insert_before_repo_setup(text,
            "\n# arm64 patch: Go uses -fuse-ld=gold for external linking on linux/arm64; alpine lacks gold\n"
            "RUN apk add --no-cache binutils-gold && ld.gold --version | head -1\n")
    if "tutanota" in base:
        # tutanota's sqlcipher fork bundles an x86-64 libcrypto.a; instances swap in the distro's arm64 one (libssl-dev)
        text = insert_before_repo_setup(text,
            "\n# arm64 patch: arm64 static libcrypto + headers for rebuilding the bundled sqlcipher module\n"
            "RUN apt-get update && apt-get install -y --no-install-recommends libssl-dev && rm -rf /var/lib/apt/lists/*\n")
    if "teleport" in base:
        # teleport's Makefile cross-compiles with CC=aarch64-linux-gnu-gcc when ARCH=arm64; on a native arm64 box
        # that is just gcc, but alpine/golang images do not provide the triplet-prefixed name.
        text = insert_before_repo_setup(text,
            "\n# arm64 patch: provide the triplet-prefixed toolchain names teleport's Makefile expects on arm64.\n"
            "# Wrapper scripts (not symlinks) so gcc keeps its own argv[0] and finds the plain ld/as.\n"
            "RUN for t in gcc g++ ld; do command -v aarch64-linux-gnu-$t >/dev/null 2>&1 || "
            "{ printf '#!/bin/sh\\nexec %s \"$@\"\\n' \"$(command -v $t)\" > /usr/local/bin/aarch64-linux-gnu-$t "
            "&& chmod +x /usr/local/bin/aarch64-linux-gnu-$t; }; done && aarch64-linux-gnu-gcc --version | head -1\n")
    if "qutebrowser" in base:
        mode, major = qutebrowser_mode(base, rows)
        log(f"[base] {base}: qutebrowser PyQt{major} mode = {mode}")
        # Distro QtWebEngine builds refuse to run as root without --no-sandbox and exit the test process silently;
        # the PyPI wheels' Chromium does not. The harness only sets this for its run-all path, so bake it in.
        text = re.sub(r"^(FROM .*\n)", r"\1ENV QTWEBENGINE_DISABLE_SANDBOX=1\n", text, count=1, flags=re.M)
        if mode == "pypi-pinned":
            text = insert_before_repo_setup(text,
                "\n# arm64 patch: runtime libraries needed by the PyQt6-Qt6 wheels' QtWebEngine (libevent, nss, xcb, ...)\n"
                "RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
                "libevent-2.1-7 libminizip1 libopus0 libwebp7 libwebpdemux2 libwebpmux3 libxslt1.1 libnss3 libnspr4 libxkbfile1 "
                "libxcomposite1 libxdamage1 libxrandr2 libxtst6 libxkbcommon0 libxkbcommon-x11-0 libasound2 libpulse0 libegl1 libgl1 "
                "libglib2.0-0 libfontconfig1 libfreetype6 libdbus-1-3 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 "
                "libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xfixes0 libsnappy1v5 libre2-9 libxcursor1 libxi6 "
                "libgssapi-krb5-2 libsm6 libice6 libwayland-egl1 libwayland-client0 libwayland-cursor0 libcups2 libgtk-3-0 "
                "libpango-1.0-0 libgdk-pixbuf-2.0-0 libspeechd2 libpq5 libodbc2 libmariadb3 curl ca-certificates "
                "&& rm -rf /var/lib/apt/lists/* \\\n"
                "    && for p in libtiff5 libwebp6; do DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $p 2>/dev/null || true; done \\\n"
                "    && cd /tmp && { ls /usr/lib/aarch64-linux-gnu/libwebp.so.6 >/dev/null 2>&1 || { curl -fsSL -O https://snapshot.debian.org/archive/debian/20250801T000000Z/pool/main/libw/libwebp/libwebp6_0.6.1-2.1+deb11u2_arm64.deb && dpkg -i libwebp6_*.deb; }; } \\\n"
                "    && { ls /usr/lib/aarch64-linux-gnu/libtiff.so.5 >/dev/null 2>&1 || { curl -fsSL -O https://snapshot.debian.org/archive/debian/20250801T000000Z/pool/main/t/tiff/libtiff5_4.2.0-1+deb11u5_arm64.deb && dpkg -i libtiff5_*.deb; }; } "
                "&& rm -f /tmp/*.deb && rm -rf /var/lib/apt/lists/*  # the 6.7 wheels link bullseye-era libwebp.so.6 / libtiff.so.5\n")
        if mode == "distro":
            core, opt = (PYQT5_CORE, PYQT5_OPT) if major == 5 else (PYQT6_CORE, PYQT6_OPT)
            if re.search(r"^FROM python:3\.8", text, re.M):
                # Debian bookworm only packages PyQt for python 3.11, and no aarch64 PyQt5 wheels exist; build PyQt5
                # 5.15.10 (+ PyQtWebEngine 5.15.7) from source against Debian's Qt 5.15.8 for the image's python 3.8.
                # Newer sip/PyQt5 releases need setuptools >= 77, which requires python >= 3.9.
                log(f"[base] {base}: python 3.8 -> PyQt5 5.15.10 built from source against distro Qt 5.15")
                text = insert_before_repo_setup(text, PYQT5_SOURCE_RUN)
            else:
                text = insert_before_repo_setup(text, PYQT_RUN.format(core=core, opt=opt, major=major))
        # Debian bookworm only ships PyQt for python3.11; python 3.9 bases move to bullseye (PyQt5 5.15.2 / py3.9).
        text = text.replace("FROM python:3.9-slim\n", "FROM python:3.9-slim-bullseye\n")
    lines = text.splitlines(keepends=True)
    for i, ln in enumerate(lines):
        if ln.startswith("FROM"):
            new = pin_from(ln.rstrip("\n"), base) + "\n"
            if new != ln:
                log(f"[base] {base}: pinned {ln.strip()} -> {new.strip()}")
            lines[i] = new + APT_FIX
            break
    text = "".join(lines)
    for needle, old, new in BASE_PATCHES:
        if needle in base:
            if old not in text:
                log(f"[base] {base}: WARNING patch text not found: {old[:60]!r}")
            text = text.replace(old, new)
    # Google Chrome has no arm64 Linux build; use Debian/Ubuntu chromium + chromium-driver instead.
    if "google-chrome" in text or "chromedriver" in text:
        text = patch_chrome(text)
    return text


CHROMIUM_RUN = (
    "# arm64 patch: Google Chrome has no arm64 Linux build; use Debian chromium + chromium-driver.\n"
    "# google-chrome is a wrapper that adds --no-sandbox (needed when running as root).\n"
    "RUN apt-get update && apt-get install -y --no-install-recommends chromium chromium-driver{extra} \\\n"
    "    && rm -rf /var/lib/apt/lists/* \\\n"
    "    && printf '#!/bin/sh\\nexec /usr/bin/chromium --no-sandbox \"$@\"\\n' > /usr/bin/google-chrome \\\n"
    "    && chmod +x /usr/bin/google-chrome \\\n"
    "    && ln -sf /usr/bin/google-chrome /usr/bin/google-chrome-stable \\\n"
    "    && ln -sf /usr/bin/chromedriver /usr/local/bin/chromedriver \\\n"
    "    && google-chrome --version && chromedriver --version\n"
)

CHROME_RE = re.compile(r"dl\.google\.com|google-chrome|chromedriver\.storage\.googleapis|chrome-for-testing-public")


def split_instructions(text):
    """Yield Dockerfile chunks: each is one instruction (with its continuation lines), or a comment/blank line."""
    cur = []
    for ln in text.splitlines(keepends=True):
        cur.append(ln)
        if ln.rstrip("\r\n").rstrip().endswith("\\"):
            continue
        yield "".join(cur)
        cur = []
    if cur:
        yield "".join(cur)


def patch_chrome(text):
    """Replace every RUN that downloads Google Chrome / upstream chromedriver with one chromium RUN."""
    out, replaced, extra = [], False, set()
    for chunk in split_instructions(text):
        if chunk.lstrip().startswith("RUN") and CHROME_RE.search(chunk):
            if "xvfb" in chunk:
                extra.add("xvfb")
            if not replaced:
                out.append("__CHROMIUM_RUN__\n")
                replaced = True
            else:
                out.append("# (removed by arm64 patch: superseded by chromium install above)\n")
        else:
            out.append(chunk)
    text = "".join(out)
    # a few bases also symlink Debian's chromedriver themselves; make that idempotent next to our wrapper
    text = text.replace("ln -s /usr/bin/chromedriver /usr/local/bin/chromedriver", "ln -sf /usr/bin/chromedriver /usr/local/bin/chromedriver")
    return text.replace("__CHROMIUM_RUN__\n", CHROMIUM_RUN.format(extra="".join(" " + e for e in sorted(extra))))


GROUP_ROWS = {}   # base -> all instance rows of that group (filled by load_plan)


def local_name(base):
    """Instance Dockerfiles sometimes FROM an ECR path like .../sweap-images/x.y:base_x__y___date.sha;
    we build and push every base under the plain `base_...` name."""
    return base.rsplit(":", 1)[-1] if "/" in base else base


LOCAL_BASE_REPO = "sweap-base"


def local_ref(base):
    """Local image ref for a base. Image names must be lowercase without '___', so the base name goes in the tag."""
    return f"{LOCAL_BASE_REPO}:{local_name(base)}"


def load_plan(repo_filter, only_ids, exclude=()):
    rows = json.load(open(ROOT / "instances.json"))
    plan = collections.OrderedDict()  # base_name -> list of rows
    for r in rows:
        if repo_filter and r["repo"] not in repo_filter:
            continue
        if r["repo"] in exclude:
            continue
        if only_ids and r["instance_id"] not in only_ids:
            continue
        iid = r["instance_id"]
        inst = (ROOT / "dockerfiles/instance_dockerfile" / iid / "Dockerfile").read_text()
        base = re.search(r"^FROM\s+(\S+)", inst, re.M).group(1)
        r["base"] = base
        plan.setdefault(base, []).append(r)
    GROUP_ROWS.update(plan)
    return plan


class Status:
    """status.json shared by concurrent build.py processes: every write is read-merge-write under flock."""
    def __init__(self, path):
        self.path = path
        self.lockpath = path.with_suffix(".lock")
        self.data = self._load()

    def _load(self):
        return json.load(open(self.path)) if self.path.exists() else {"base": {}, "instance": {}}

    def get(self, kind, key):
        return self.data[kind].get(key)

    def set(self, kind, key, **kw):
        with lock, open(self.lockpath, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            self.data = self._load()
            self.data[kind][key] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **kw}
            tmp = self.path.with_suffix(f".tmp{os.getpid()}")
            json.dump(self.data, open(tmp, "w"), indent=1)
            os.replace(tmp, self.path)
            fcntl.flock(lf, fcntl.LOCK_UN)


MIN_FREE_GB = 120
prune_lock = threading.Lock()


def disk_watchdog():
    """BuildKit keeps layers of pushed-then-removed images in its cache; prune when the disk gets low."""
    free_gb = shutil.disk_usage("/var/lib/docker").free / 1e9
    if free_gb >= MIN_FREE_GB:
        return
    with prune_lock:
        free_gb = shutil.disk_usage("/var/lib/docker").free / 1e9
        if free_gb >= MIN_FREE_GB:
            return
        log(f"[disk] {free_gb:.0f} GB free < {MIN_FREE_GB}; pruning build cache and dangling images")
        subprocess.run(["docker", "builder", "prune", "-f"], capture_output=True)
        subprocess.run(["docker", "image", "prune", "-f"], capture_output=True)
        log(f"[disk] now {shutil.disk_usage('/var/lib/docker').free / 1e9:.0f} GB free")


def build_base(base, iid, args, status, rows=()):
    """Build local image tagged local_name(base); instance Dockerfiles are rewritten to FROM that name."""
    base = local_name(base)
    ref = local_ref(base)
    if not args.force and not args.rebuild_base and (status.get("base", base) or {}).get("ok") and image_exists(ref):
        log(f"[base] {base}: cached")
        return True
    src = ROOT / "dockerfiles/base_dockerfile" / iid / "Dockerfile"
    wd = ROOT / "work/base" / base
    wd.mkdir(parents=True, exist_ok=True)
    patched = patch_base(src.read_text(), base, rows)
    (wd / "Dockerfile").write_text(patched)
    from_ref = re.search(r"^FROM\s+(\S+)", patched, re.M).group(1)
    if not image_exists(from_ref) and not tag_exists(from_ref):
        log(f"[base] {base}: FROM image {from_ref} not available for arm64")
        status.set("base", base, ok=False, error=f"FROM {from_ref} unavailable")
        return False
    logfile = ROOT / "logs/base" / f"{base}.log"
    logfile.parent.mkdir(parents=True, exist_ok=True)
    logfile.write_text("")
    t0 = time.time()
    log(f"[base] {base}: building")
    for attempt in range(12):
        rc = run(["docker", "build", "--platform", PLATFORM, "--progress=plain", "-t", ref, str(wd)],
                 logfile, timeout=args.timeout)
        if rc == 0 or "toomanyrequests" not in logfile.read_text()[-4000:]:
            break
        log(f"[base] {base}: Docker Hub pull rate limited; waiting 5 min (attempt {attempt + 1}/12)")
        time.sleep(300)
    dur = round(time.time() - t0)
    if rc != 0:
        log(f"[base] {base}: FAILED ({dur}s) see {logfile}")
        status.set("base", base, ok=False, seconds=dur)
        return False
    log(f"[base] {base}: built ({dur}s)")
    pushed = True
    if args.push:
        remote = f"{args.namespace}:{base}"
        subprocess.run(["docker", "tag", ref, remote], check=True)
        rc = run(["docker", "push", remote], logfile)
        subprocess.run(["docker", "rmi", remote], capture_output=True)
        pushed = rc == 0
        log(f"[base] {base}: {'pushed' if pushed else 'PUSH FAILED'}")
    status.set("base", base, ok=True, pushed=pushed, seconds=dur)
    return True


def base_vanished(iid):
    """True if the instance build failed only because its local sweap-base image was gone."""
    try:
        tail = (ROOT / "logs/instance" / f"{iid}.log").read_text()[-3000:]
    except OSError:
        return False
    return "failed to resolve source metadata" in tail and LOCAL_BASE_REPO in tail


def image_exists(ref):
    return subprocess.run(["docker", "image", "inspect", ref], capture_output=True).returncode == 0


PIP_PYQT_TOKEN = re.compile(r"\s+(?:-r\s+\S*requirements-pyqt\S*|PyQt\S*)")
TAG_DATES = ROOT / "tag_dates.json"
_tag_dates = json.load(open(TAG_DATES)) if TAG_DATES.exists() else {}


# pypi-timemachine is installed from live PyPI before the build script switches pip to the dated index, so its
# dependency tree (anyio, starlette, uvicorn, fastapi...) lands in the test environment at today's versions; today's
# anyio ships a pytest plugin that breaks pytest < 7. Install it into its own venv instead, and keep only the
# anyio version the official images (built Aug 2025) carry in site-packages.
TIMEMACHINE_INSTALL = ("{ python -m venv /opt/pypi-timemachine && /opt/pypi-timemachine/bin/pip install -q pypi-timemachine "
                       "&& ln -sf /opt/pypi-timemachine/bin/pypi-timemachine /usr/local/bin/pypi-timemachine "
                       "&& pip install anyio==4.10.0; } || pip install pypi-timemachine")


def patch_instance(text, row):
    text = re.sub(r"^FROM\s+(\S+)", lambda m: "FROM " + local_ref(m.group(1)), text, count=1, flags=re.M)
    text = re.sub(r"^(\s*)pip install pypi-timemachine\s*$", r"\1" + TIMEMACHINE_INSTALL + "  # arm64 rebuild: isolated install, see build.py",
                  text, count=1, flags=re.M)
    # preprocess.sh resets to the instance commit; make sure it is present in the base's clone first
    text = re.sub(r"^(git (?:reset --hard|checkout) ([0-9a-f]{40}))\s*$",
                  r"git cat-file -e \2^{commit} 2>/dev/null || git fetch -q origin \2 || true\n\1", text, count=1, flags=re.M)
    # 53 element-web Dockerfiles in the public repo are truncated: the build heredoc ends with a bare EOF and the
    # chmod/run lines are missing. The official images do contain and run /build.sh, so restore that.
    if "<<'EOFBUILD'" in text and not re.search(r"^EOFBUILD\s*$", text, re.M):
        text = re.sub(r"^EOF\s*$(?![\s\S]*^EOF\s*$)", "EOFBUILD\nRUN chmod +x /build.sh\nRUN /build.sh\n", text, count=1, flags=re.M)
        if not re.search(r"^EOFBUILD\s*$", text, re.M):
            text = text.rstrip("\n") + "\nEOFBUILD\nRUN chmod +x /build.sh\nRUN /build.sh\n"
    # Unlocked `npm install` resolves the newest versions today; the official image resolved them when it was
    # built. npm's `before` config replays resolution as of the official image's push date.
    pushed = (_tag_dates.get(row["dockerhub_tag"]) or {}).get("tag_last_pushed")
    if row["repo"] in LOCKFILE_REPOS:
        # the official image's lockfile is copied in right before the build script runs (after git clean)
        text = re.sub(r"^(RUN /build\.sh\s*)$", "# arm64 rebuild: dependency tree of the official image\n"
                      "COPY package-lock.json /app/package-lock.json\n\\1", text, count=1, flags=re.M)
    elif pushed and re.search(r"\bnpm (install|i)\b", text):
        text = re.sub(r"^(FROM .*\n)", r"\1# arm64 rebuild: resolve npm packages as of the official image's build date\n"
                      f"ENV npm_config_before={pushed}\n", text, count=1, flags=re.M)
    if row["repo"] == "tutao/tutanota":
        # The git dependency tutao/better-sqlite3-sqlcipher bundles an x86-64-only libcrypto.a (plus OpenSSL headers)
        # in deps/sqlite3.tar.gz, and tutanota's test runner recompiles the module at test time. Swap in Debian's
        # arm64 static libcrypto and headers inside the tarball. Where npm ci itself runs the module's build script
        # (older fork commits), fall back to an install without scripts and rebuild after patching.
        fix = ("SQ=node_modules/better-sqlite3/deps/sqlite3.tar.gz; "
               "if [ -f \"$SQ\" ] && tar -tzf \"$SQ\" | grep -q 'OpenSSL-Linux/libcrypto.a'; then "
               "rm -rf /tmp/sq && mkdir -p /tmp/sq && tar -xzf \"$SQ\" -C /tmp/sq "
               "&& cp /usr/lib/aarch64-linux-gnu/libcrypto.a /tmp/sq/OpenSSL-Linux/libcrypto.a "
               "&& rm -rf /tmp/sq/openssl-include && mkdir -p /tmp/sq/openssl-include "
               "&& cp -r /usr/include/openssl /tmp/sq/openssl-include/ "
               "&& cp /usr/include/aarch64-linux-gnu/openssl/*.h /tmp/sq/openssl-include/openssl/ "
               "&& tar -czf \"$SQ\" -C /tmp/sq . && rm -rf /tmp/sq && echo 'arm64 patch: sqlcipher tarball now carries arm64 libcrypto'; fi; "
               "if [ -n \"$NPM_REBUILD\" ]; then npm rebuild; fi")
        text = re.sub(r"^(\s*)npm ci(\s*)$", r"\1npm ci || { npm ci --ignore-scripts && NPM_REBUILD=1; }\n\1" + fix + "  # arm64 patch",
                      text, count=1, flags=re.M)
        text = re.sub(r"^(\s*)(npm ci --ignore-scripts\s*)$", r"\1\2\n\1" + fix + "  # arm64 patch", text, count=1, flags=re.M)
    if row["repo"] == "ansible/ansible":
        # Unpinned `cryptography` at dates before 2020-08-27 resolves to 2.9.x, which has x86_64 wheels but no
        # aarch64 wheel and does not compile against OpenSSL 3. Pre-install 3.1 (first aarch64 wheel) from PyPI.
        tm = timemachine_date(row["instance_id"])
        if tm and tm < "2020-08-27" and "cryptography" in text:
            text = re.sub(r"^(pip config set global\.index-url \S+\s*\n)",
                          r"\1pip install --index-url https://pypi.org/simple cryptography==3.1  # arm64 patch: first aarch64 wheel\n",
                          text, count=1, flags=re.M)
    if row["repo"] == "protonmail/webclients":
        # puppeteer's postinstall downloads a Chromium build that does not exist for linux/arm64; the unit tests
        # (jest) do not launch it. Skip the download and point it at the distro chromium if a test ever asks.
        text = re.sub(r"^(FROM .*\n)", r"\1# arm64 rebuild: puppeteer has no linux/arm64 Chromium download\n"
                      "ENV PUPPETEER_SKIP_DOWNLOAD=1 PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=1 PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium-browser\n",
                      text, count=1, flags=re.M)
    if row["repo"] == "gravitational/teleport":
        # build scripts hardcode GOARCH=amd64 for the teleport binary (native on Scale's builders); build natively here
        text, n = re.subn(r"\bGOARCH=amd64\b", "GOARCH=arm64", text)
        if n:
            text = text.replace("RUN /build.sh", "# arm64 patch: GOARCH=amd64 -> arm64 in build.sh\nRUN /build.sh", 1)
    if row["repo"] == "qutebrowser/qutebrowser":
        mode, major = qutebrowser_mode(row["base"], [row])
        if mode == "pypi":
            return text
        if mode == "pypi-pinned":
            text = re.sub(r"^(\s*)(pip3? install\b[^\n]*PyQt6[^\n]*)$",
                          lambda m: m.group(1) + "pip install --index-url https://pypi.org/simple PyQt6==6.7.1 PyQt6-Qt6==6.7.3 PyQt6-sip==13.8.0 PyQt6-WebEngine==6.7.0 PyQt6-WebEngine-Qt6==6.7.3"
                          "  # arm64 patch: first PyQt6 aarch64 wheels\n" + m.group(1)
                          + "QT_QPA_PLATFORM=offscreen python -c 'from PyQt6 import QtWebEngineWidgets, QtWebEngineCore; print(\"PyQt6 WebEngine import ok\")'  # arm64 patch\n"
                          + m.group(1) + m.group(2),
                          text, count=1, flags=re.M)
        # adblock < 0.4.4 has no aarch64 wheel (and no sdist); swap such pins for 0.4.4 (same 0.4 API) from PyPI directly.
        adblock_fix = ("if grep -qE '^adblock==0\\.(4\\.[0-3]|[0-3]\\.)' requirements.txt 2>/dev/null; then "
                       "sed -i '/^adblock/d' requirements.txt && pip install --index-url https://pypi.org/simple adblock==0.4.4; fi"
                       "  # arm64 patch\n")
        text = re.sub(r"^(\s*pip3? install -r requirements\.txt\s*$)", lambda m: adblock_fix + m.group(1), text, count=1, flags=re.M)
        out = []
        for ln in text.splitlines(keepends=True):
            if re.match(r"\s*pip3? install\b", ln) and re.search(r"PyQt|requirements-pyqt", ln) and "arm64 patch" not in ln:
                new = PIP_PYQT_TOKEN.sub("", ln.rstrip("\n"))
                if re.match(r"\s*pip3? install(\s+-[-\w]+)*\s*$", new):
                    new = "true  # arm64 patch: PyQt comes from the distro (was: " + ln.strip()[:80] + ")"
                else:
                    new += "  # arm64 patch: PyQt tokens removed"
                out.append(new + "\n")
            else:
                out.append(ln)
        text = "".join(out)
    return text


LOCKFILE_REPOS = {"NodeBB/NodeBB"}   # repos whose build runs an unlocked `npm install`


def hub_pull(ref, platform):
    """docker pull with Docker Hub rate-limit waits. Returns True on success."""
    for attempt in range(12):
        p = subprocess.run(["docker", "pull", "-q", "--platform", platform, ref], capture_output=True, text=True)
        out = p.stdout + p.stderr
        if p.returncode == 0:
            return True
        if "toomanyrequests" in out or "rate limit" in out.lower():
            log(f"[hub] rate limited pulling {ref}; waiting 5 min (attempt {attempt + 1}/12)")
            time.sleep(300)
            continue
        log(f"[hub] pull {ref} failed: {out.strip()[-200:]}")
        return False
    return False


def fetch_official_lockfile(row, wd):
    """Copy /app/package-lock.json out of the official amd64 image so npm installs the same dependency tree."""
    dst = wd / "package-lock.json"
    if dst.exists():
        return True
    ref = f"jefzda/sweap-images:{row['dockerhub_tag']}"
    if not hub_pull(ref, "linux/amd64"):
        return False
    p = subprocess.run(["docker", "create", "--platform", "linux/amd64", ref], capture_output=True, text=True)
    cid = p.stdout.strip()
    ok = cid and subprocess.run(["docker", "cp", f"{cid}:/app/package-lock.json", str(dst)], capture_output=True).returncode == 0
    if cid:
        subprocess.run(["docker", "rm", cid], capture_output=True)
    subprocess.run(["docker", "rmi", ref], capture_output=True)
    if not ok:
        log(f"[inst] {row['instance_id']}: official image has no /app/package-lock.json")
    return bool(ok)


def build_instance(row, args, status):
    iid, tag = row["instance_id"], row["dockerhub_tag"]
    remote = f"{args.namespace}:{tag}"
    prev = status.get("instance", iid) or {}
    stale = bool(args.force_before) and prev.get("ts", "") < args.force_before
    if not args.force and not stale and prev.get("ok") and (prev.get("pushed") or not args.push):
        log(f"[inst] {iid}: done previously")
        return True
    src_df = (ROOT / "dockerfiles/instance_dockerfile" / iid / "Dockerfile").read_text()
    wd = ROOT / "work/instance" / iid
    wd.mkdir(parents=True, exist_ok=True)
    if row["repo"] in LOCKFILE_REPOS and not fetch_official_lockfile(row, wd):
        log(f"[inst] {iid}: FAILED (no official lockfile)")
        status.set("instance", iid, ok=False, error="no official lockfile", tag=tag)
        return False
    (wd / "Dockerfile").write_text(patch_instance(src_df, row))
    logfile = ROOT / "logs/instance" / f"{iid}.log"
    logfile.parent.mkdir(parents=True, exist_ok=True)
    logfile.write_text("")
    disk_watchdog()
    t0 = time.time()
    log(f"[inst] {iid}: building")
    rc = run(["docker", "build", "--platform", PLATFORM, "--progress=plain", "-t", remote, str(wd)],
             logfile, timeout=args.timeout)
    dur = round(time.time() - t0)
    if rc != 0:
        log(f"[inst] {iid}: FAILED ({dur}s)")
        status.set("instance", iid, ok=False, seconds=dur, tag=tag)
        return False
    pushed = True
    if args.push:
        rc = run(["docker", "push", remote], logfile)
        pushed = rc == 0
        log(f"[inst] {iid}: built ({dur}s), {'pushed' if pushed else 'PUSH FAILED'}")
        if pushed and not args.keep:
            subprocess.run(["docker", "rmi", remote], capture_output=True)
    else:
        log(f"[inst] {iid}: built ({dur}s)")
    status.set("instance", iid, ok=True, pushed=pushed, seconds=dur, tag=tag)
    return pushed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", action="append", help="only these repos (e.g. navidrome/navidrome); repeatable")
    ap.add_argument("--instance", action="append", help="only these instance ids; repeatable")
    ap.add_argument("--exclude-repo", action="append", default=[], help="skip these repos; repeatable")
    ap.add_argument("--namespace", default="bdqnghi/sweap-images")
    ap.add_argument("--jobs", type=int, default=4, help="parallel instance builds")
    ap.add_argument("--base-jobs", type=int, default=2, help="parallel base builds")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep instance images locally after push")
    ap.add_argument("--keep-base", action="store_true", help="keep base images after their instances finish")
    ap.add_argument("--force", action="store_true", help="rebuild even if status says ok")
    ap.add_argument("--force-before", default="", help="rebuild instances whose status timestamp is older than this (YYYY-MM-DDTHH:MM:SS)")
    ap.add_argument("--rebuild-base", action="store_true", help="rebuild bases even if cached (instances still skipped if done)")
    ap.add_argument("--timeout", type=int, default=3 * 3600, help="per-build timeout in seconds")
    ap.add_argument("--plan-only", action="store_true")
    args = ap.parse_args()

    plan = load_plan(set(args.repo or []), set(args.instance or []), set(args.exclude_repo))
    n = sum(len(v) for v in plan.values())
    log(f"plan: {len(plan)} bases, {n} instances -> {args.namespace} (push={args.push})")
    if args.plan_only:
        for b, rows in plan.items():
            print(f"  {b}: {len(rows)}")
        return

    status = Status(ROOT / "status.json")
    results = {"base_fail": [], "inst_fail": [], "inst_ok": 0}

    def do_group(base, rows):
        if not build_base(base, rows[0]["instance_id"], args, status, rows):
            results["base_fail"].append(base)
            return
        pending = list(rows)
        for attempt in range(3):
            retry = []
            with ThreadPoolExecutor(max_workers=args.jobs) as ex:
                futs = {ex.submit(build_instance, r, args, status): r for r in pending}
                for f in as_completed(futs):
                    r = futs[f]
                    try:
                        ok = f.result()
                    except Exception as e:  # timeout etc.
                        log(f"[inst] {r['instance_id']}: EXCEPTION {e}")
                        status.set("instance", r["instance_id"], ok=False, error=str(e), tag=r["dockerhub_tag"])
                        ok = False
                    if ok:
                        with lock:
                            results["inst_ok"] += 1
                    elif base_vanished(r["instance_id"]):
                        retry.append(r)
                    else:
                        with lock:
                            results["inst_fail"].append(r["instance_id"])
            if not retry:
                break
            log(f"[base] {local_name(base)}: image disappeared during group; rebuilding and retrying {len(retry)} instances")
            if not build_base(base, rows[0]["instance_id"], args, status, rows):
                with lock:
                    results["inst_fail"].extend(r["instance_id"] for r in retry)
                break
            pending = retry
        else:
            with lock:
                results["inst_fail"].extend(r["instance_id"] for r in pending)
        if not args.keep_base:
            subprocess.run(["docker", "rmi", local_ref(base)], capture_output=True)

    # Groups run with --base-jobs concurrency; each group runs its instances with --jobs concurrency.
    with ThreadPoolExecutor(max_workers=args.base_jobs) as ex:
        list(ex.map(lambda kv: do_group(*kv), plan.items()))

    log(f"DONE ok={results['inst_ok']} inst_fail={len(results['inst_fail'])} base_fail={len(results['base_fail'])}")
    for b in results["base_fail"]:
        print("  base failed:", b)
    for i in results["inst_fail"]:
        print("  instance failed:", i)
    sys.exit(1 if results["base_fail"] or results["inst_fail"] else 0)


if __name__ == "__main__":
    main()
