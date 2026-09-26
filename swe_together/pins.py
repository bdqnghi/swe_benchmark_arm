"""Drift control: pin what the official amd64 image resolved (fingerprints/<task>.json, from fingerprint.py).

The task Dockerfiles install several things unpinned (bun's installer, `npm install -g pnpm`, NodeSource's
`nodejs`, `pip install` without a lock). A rebuild months later resolves newer versions, and hidden tests can
change behaviour (e.g. bun 1.4 JUnit reports classnames differently from bun 1.3.14, which zeroes F2P matches).
This module rewrites those steps to the exact versions of the official image:

  * bun installer            -> `bash -s "bun-v<official>"`
  * npm install -g <pkg>...  -> `<pkg>@<official>` for every package present in the official global npm tree
  * NodeSource nodejs        -> `nodejs=<official dpkg version>`
  * pip                      -> a constraints file (the official image's Python distributions, build backends
                                and editable/local installs excluded, local version labels such as `+cpu`
                                dropped) passed as a build ARG `PIP_CONSTRAINT`, so it applies to every RUN of
                                the build but is not part of the runtime environment.
"""
import json, re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONSTRAINTS_NAME = "arm64-pip-constraints.txt"
CONSTRAINTS_PATH = "/opt/swe-together-arm64/pip-constraints.txt"

BUILD_BACKENDS = {
    "pip", "setuptools", "wheel", "hatchling", "hatch-vcs", "flit-core", "poetry-core", "pdm-backend",
    "setuptools-scm", "scikit-build-core", "meson-python", "meson", "maturin", "cmake", "ninja", "cython",
    "packaging", "pybind11", "distlib", "virtualenv", "uv",
}


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def load(task):
    p = HERE / "fingerprints" / f"{task}.json"
    return json.loads(p.read_text()) if p.exists() else None


def bare_version(tool_line):
    m = re.search(r"(\d+\.\d+\.\d+)", tool_line or "")
    return m.group(1) if m else None


def constraints(fp, skip=()):
    """Constraint lines from the official image's Python distributions."""
    from packaging.version import Version, InvalidVersion
    skip = {norm(s) for s in skip}
    seen = {}
    pys = sorted(fp.get("pythons", {}).values(), key=lambda p: -len(p.get("dists", [])))
    for py in pys:
        for d in py.get("dists", []):
            if not d.get("name"):
                continue
            n = norm(d["name"])
            du = d.get("direct_url") or {}
            if n in BUILD_BACKENDS or n in skip or du.get("dir_info", {}).get("editable") or \
                    str(du.get("url", "")).startswith("file:") or "vcs_info" in du:
                continue
            if "/usr/lib/python3/dist-packages" in d.get("path", ""):
                continue  # Debian/Ubuntu apt package, not pip-resolved
            try:
                v = Version(d["version"])
            except InvalidVersion:
                continue
            v = v.public  # drop +cpu etc.
            if n in seen and seen[n] != v:
                seen[n] = None  # environments disagree: leave unpinned
            elif n not in seen:
                seen[n] = v
    return [f"{n}=={v}" for n, v in sorted(seen.items()) if v]


def apply(task, text, ctx, fp, skip_constraints=()):
    """Rewrite Dockerfile text; write any extra files into ctx. Returns (text, [notes])."""
    notes = []
    if not fp:
        return text, ["no official fingerprint: unpinned"]
    tools = fp.get("tools", {})
    # bun
    bv = bare_version(tools.get("bun", {}).get("version"))
    if bv:
        text, n = re.subn(r"(https://bun\.sh/install\"?\s*\|\s*(?:[A-Z_]+=\S+\s+)*bash)\b(?!\s+-s)",
                          rf'\1 -s "bun-v{bv}"', text)
        if n:
            notes.append(f"bun pinned to {bv}")
    # npm -g
    npmg = fp.get("npm_global") or {}

    def pin_npm(m):
        out = []
        for tok in m.group(2).split():
            if tok.startswith("-"):
                out.append(tok)
                continue
            name = tok if tok.startswith("@") and tok.count("@") == 1 else tok.rsplit("@", 1)[0] if "@" in tok[1:] else tok
            if name in npmg and npmg[name] and tok != f"{name}@{npmg[name]}":
                out.append(f"{name}@{npmg[name]}")
                notes.append(f"npm -g {tok} -> {name}@{npmg[name]}")
            else:
                out.append(tok)
        return m.group(1) + " ".join(out)
    text = re.sub(r"(npm\s+(?:install|i)\s+(?:--?\S+\s+)*-g\s+)((?:[@\w./-]+(?:@[\w.^~*-]+)?[ \t]*)+?)(?=[ \t]*(?:&&|\\|\n|;|\|\||$))",
                  pin_npm, text)
    # NodeSource nodejs
    nodedeb = fp.get("dpkg", {}).get("nodejs", "")
    if "nodesource" in text and "nodesource" in nodedeb:
        text, n = re.subn(r"(apt-get\s+install[^\n]*?\s)nodejs(?=[\s\\]|$)", rf"\1nodejs={nodedeb}", text)
        if n:
            notes.append(f"nodejs pinned to {nodedeb}")
    # pip constraints
    if re.search(r"\bpip3?\b[^\n]*\binstall\b|-m pip install", text):
        lines = constraints(fp, skip_constraints)
        if lines:
            (ctx / CONSTRAINTS_NAME).write_text("\n".join(lines) + "\n")
            out, done = [], False
            for line in text.splitlines():
                out.append(line)
                if re.match(r"^FROM\s", line, re.I):
                    out += [f"COPY {CONSTRAINTS_NAME} {CONSTRAINTS_PATH}",
                            f"ARG PIP_CONSTRAINT={CONSTRAINTS_PATH}"]
                    done = True
            text = "\n".join(out) + "\n"
            if done:
                notes.append(f"pip constraints from official image ({len(lines)} pins)")
    return text, notes
