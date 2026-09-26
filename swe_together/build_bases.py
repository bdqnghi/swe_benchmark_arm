#!/usr/bin/env python3
"""Build the four private SWE-Together base images for arm64 and push them as bdqnghi/swe-together-base:<name>.

The official bases (ghcr.io/togetherbench/togetherbench/{hyperswitch,reigh,comfyui,sd-scripts}-dev:latest) are
not pullable and have no published Dockerfile. bases/<name>.Dockerfile is their reconstruction from the BuildKit
history of one official task image built on them (the base layers are the bottom of that history; see the header
of each file). Drift control: pins.py is applied with the fingerprint of that same official task image (NodeSource
nodejs version, pip constraints for everything the base installs), and each clone is checked out at the default
branch's last commit before the official base was built.

  python -u build_bases.py [--base NAME] [--push]
"""
import argparse, shutil, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pins  # noqa: E402

REPRESENTATIVE = {  # base -> official task image whose history the reconstruction came from
    "hyperswitch-dev": "hyperswitch-8084",
    "reigh-dev": "reigh-preset-data-flow",
    "comfyui-dev": "comfyui-gemma3-sliding-window",
    "sd-scripts-dev": "sd-scripts-fp8-lumina",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", action="append")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()
    for base in a.base or REPRESENTATIVE:
        ctx = HERE / "work" / f"base-{base}"
        shutil.rmtree(ctx, ignore_errors=True)
        ctx.mkdir(parents=True)
        text = (HERE / "bases" / f"{base}.Dockerfile").read_text()
        text, notes = pins.apply(base, text, ctx, pins.load(REPRESENTATIVE[base]))
        (ctx / "Dockerfile").write_text(text)
        ref = f"bdqnghi/swe-together-base:{base}"
        log = HERE / "logs" / f"base-{base}.log"
        print(time.strftime("%H:%M:%S"), base, "building:", "; ".join(notes) or "no pins", flush=True)
        for attempt in range(3):
            with open(log, "wb") as f:
                rc = subprocess.run(["docker", "build", "--platform", "linux/arm64", "--progress=plain", "-t", ref,
                                     str(ctx)], stdout=f, stderr=subprocess.STDOUT).returncode
            if rc == 0:
                break
            time.sleep(60)
        print(time.strftime("%H:%M:%S"), base, "built" if rc == 0 else f"FAILED see {log}", flush=True)
        if rc == 0 and a.push:
            for attempt in range(4):
                if subprocess.run(["docker", "push", ref], capture_output=True).returncode == 0:
                    print(time.strftime("%H:%M:%S"), base, "pushed", flush=True)
                    break
                time.sleep(60)


if __name__ == "__main__":
    main()
