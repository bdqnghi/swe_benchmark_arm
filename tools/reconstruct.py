#!/usr/bin/env python3
"""Reconstruct a buildable Dockerfile from an existing (BuildKit-built) image's layer history.

Usage:
  reconstruct.py <image> <outdir> [--from BASE] [--copy /path/in/image ...] [--skip REGEX ...] [--replace OLD=NEW ...]

Writes <outdir>/Dockerfile plus one tarball per --copy path (extracted from the image with `docker cp`) that the
Dockerfile ADDs back in place of the history steps you --skip (e.g. a private `git clone`). The history must come
from BuildKit (it records each instruction's text); `#(nop)` metadata layers are converted to their instruction.
"""
import argparse, json, re, shlex, subprocess, sys
from pathlib import Path


def history(image):
    out = subprocess.run(["docker", "history", "--no-trunc", "--format", "{{json .}}", image],
                         capture_output=True, text=True, check=True).stdout
    rows = [json.loads(l) for l in out.splitlines() if l.strip()]
    rows.reverse()  # oldest first
    return [r["CreatedBy"] for r in rows]


def fix_env_quotes(inst):
    """history prints `ENV K=a b c` unquoted; Dockerfile needs ENV K="a b c" when the value has spaces."""
    m = re.match(r"^(ENV|ARG) ([A-Za-z_][A-Za-z0-9_]*)=(.*)$", inst, re.S)
    if m and " " in m.group(3) and not re.match(r'^"[^"]*"$', m.group(3)) and "=" not in m.group(3).split(" ", 1)[1].split(" ")[0]:
        val = m.group(3)
        # several KEY=VAL pairs on one line -> leave alone unless no other token looks like KEY=VAL
        if not re.search(r"\s[A-Za-z_][A-Za-z0-9_]*=", val):
            return f'{m.group(1)} {m.group(2)}="{val.replace(chr(92), chr(92)*2).replace(chr(34), chr(92)+chr(34))}"'
    return inst


def fix_exec_form(inst):
    """history prints exec forms without quotes (SHELL [/bin/bash -c]); Dockerfile needs JSON.
    It also prints EXPOSE as a Go map (EXPOSE map[6379/tcp:{}]) and VOLUME as [/data]."""
    m2 = re.match(r"(SHELL|CMD|ENTRYPOINT|VOLUME) \[(.*)\]$", inst.strip())
    if m2 and '"' not in m2.group(2):
        return f"{m2.group(1)} {json.dumps(m2.group(2).split())}"
    m3 = re.match(r"EXPOSE map\[(.*)\]$", inst.strip())
    if m3:
        ports = re.findall(r"([\w./-]+):\{\}", m3.group(1))
        return "EXPOSE " + " ".join(ports)
    return inst


def to_instruction(created_by, config=None):
    """Map one history line to a Dockerfile instruction (or None to drop)."""
    s = created_by.strip()
    config = config or {}
    # some builders record exec-form CMD/ENTRYPOINT as the bare joined command ("tail -f /dev/null")
    for key, inst in (("Cmd", "CMD"), ("Entrypoint", "ENTRYPOINT")):
        if config.get(key) and s == " ".join(config[key]):
            return f"{inst} {json.dumps(config[key])}"
    m = re.match(r"/bin/sh -c #\(nop\)\s+(.*)", s)
    if m:
        inst = m.group(1).strip()
        if re.match(r"(ADD|COPY) (file|dir|multi):", inst):
            return None  # base image rootfs or a COPY we cannot recover; handled by --from / --copy
        if inst.startswith(("LABEL", "MAINTAINER")):
            return None
        return fix_env_quotes(fix_exec_form(inst))  # ENV / ARG / WORKDIR / USER / CMD / ENTRYPOINT / EXPOSE / VOLUME / SHELL
    m = re.match(r"/bin/sh -c (.*?)(?: # buildkit)?$", s, re.S)
    if m:
        return "RUN " + m.group(1).strip()
    m = re.match(r"RUN (\|\d+ (?:\S+=\S+ ?)+)?(?:/bin/sh -c |/bin/bash -c )?(.*?)(?: # buildkit)?$", s, re.S)
    if m:  # BuildKit RUN with build args prefix
        return "RUN " + m.group(2).strip()
    if s.startswith(("ENV ", "ARG ", "WORKDIR ", "USER ", "CMD ", "ENTRYPOINT ", "EXPOSE ", "SHELL ", "LABEL ", "VOLUME ")):
        return None if s.startswith("LABEL ") else fix_env_quotes(fix_exec_form(s))
    if s.startswith(("COPY ", "ADD ")):
        return None
    # BuildKit can also record the raw shell string (custom SHELL); treat as RUN
    return "RUN " + s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("outdir")
    ap.add_argument("--from", dest="base", required=True, help="FROM line to use (the base rootfs layer is not recoverable)")
    ap.add_argument("--copy", action="append", default=[], help="path inside the image to extract and ADD back")
    ap.add_argument("--skip", action="append", default=[], help="regex: drop history steps matching it")
    ap.add_argument("--replace", action="append", default=[], help="OLD=NEW literal replacement applied to every step")
    ap.add_argument("--platform", default="linux/amd64", help="platform of the source image (for docker create)")
    ap.add_argument("--skip-until", default="", help="regex: drop every step up to and including the first match "
                    "(use when --from already contains those layers, e.g. an official python image)")
    args = ap.parse_args()
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)

    steps = []
    config = json.loads(subprocess.run(["docker", "inspect", "--format", "{{json .Config}}", args.image],
                                       capture_output=True, text=True, check=True).stdout)
    hist = [to_instruction(h, config) for h in history(args.image)]
    hist = [h for h in hist if h is not None]
    if args.skip_until:
        for i, h in enumerate(hist):
            if re.search(args.skip_until, h):
                hist = hist[i + 1:]
                break
    for inst in hist:
        if any(re.search(p, inst) for p in args.skip):
            steps.append("# skipped: " + inst.replace("\n", " ")[:160])
            continue
        for rep in args.replace:
            old, new = rep.split("=", 1)
            inst = inst.replace(old, new)
        steps.append(inst)

    copies = []
    if args.copy:
        cid = subprocess.run(["docker", "create", "--platform", args.platform, args.image],
                             capture_output=True, text=True, check=True).stdout.strip()
        try:
            for i, p in enumerate(args.copy):
                tar = out / f"copy{i}.tar"
                with open(tar, "wb") as f:
                    subprocess.run(["docker", "cp", f"{cid}:{p}", "-"], stdout=f, check=True)
                copies.append((tar.name, p))
        finally:
            subprocess.run(["docker", "rm", cid], capture_output=True)

    lines = ["# Reconstructed from `docker history " + args.image + "` by reconstruct.py", "FROM " + args.base]
    lines += steps
    for name, p in copies:
        parent = str(Path(p).parent)
        # docker cp of a directory produces <basename>/...; ADD extracts the tar into the parent directory
        lines.append(f"ADD {name} {parent.rstrip(chr(47))}/")
    (out / "Dockerfile").write_text("\n".join(lines) + "\n")
    print(f"wrote {out/'Dockerfile'} ({len(steps)} steps, {len(copies)} copied paths)")


if __name__ == "__main__":
    main()
