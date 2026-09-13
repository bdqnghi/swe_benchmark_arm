# SWE benchmarks on arm64

Native `linux/arm64` Docker images for five agentic coding benchmarks whose official images are
`linux/amd64`-only, plus the scripts that built them and the patches needed to run the benchmark
harnesses against them. Everything here was produced and validated on an NVIDIA GB10 (DGX Spark,
aarch64, 20 cores, 121 GB RAM) so that evaluation no longer has to run under `qemu-x86_64`.

All images are public on Docker Hub under the `bdqnghi` namespace.

| Benchmark | Instances | arm64 images | Validation on arm64 |
|---|---|---|---|
| SWE-bench Pro (scaleapi) | 731 | `bdqnghi/sweap-images:<dockerhub_tag>` + 69 base images | 731/731 gold patches pass with the official harness |
| DeepSWE (datacurve) | 113 | `bdqnghi/deepswe:<task_id>` | 113/113 oracle (reference solution) pass under Pier/Harbor |
| ProgramBench (facebookresearch) | 200 | `bdqnghi/<org>_1776_<repo>.<sha>:task_cleanroom_v6` | toolchain/package parity with the official images on all 200; end-to-end `programbench eval` gold runs pass (e.g. cmatrix, quickjs) |
| SWE-Bench-ProMax | 170 | `bdqnghi/swebench-promax:<instance_id>` | 170/170 gold patches pass with the official `test_run.py` (offline) |
| Terminal-Bench 3.0 | 74 | `bdqnghi/terminal-bench-v3:<task>` and `:<task>-verifier` | Harbor oracle pass on 72/74; fp8-rmsnorm-gemm and jax-speedrun-gpu need H100 hardware |
| Terminal-Bench 4.0 | 66 | `bdqnghi/terminal-bench-v4:<task>` and `:<task>-verifier` | Harbor oracle pass on 64/66; the same two tasks need H100 hardware |

Terminal-Bench 3.0 and 4.0 are kept in separate repositories; a v4 run never pulls a v3 image.

### Not validated: the four H100-only Terminal-Bench tasks

The four unvalidated Terminal-Bench tasks are `fp8-rmsnorm-gemm` and `jax-speedrun-gpu`, in each of
3.0 and 4.0. Both declare H100-only hardware (`gpu_types = ["H100"]`): the fp8 task's reference kernel
uses Hopper-only `wgmma` instructions, and the speedrun task's grader enforces a wall-clock budget
calibrated on an H100. They therefore cannot pass on the GB10 (Blackwell) this work was validated on,
or on any non-Hopper GPU, and need an arm64 Hopper host such as a GH200. Their arm64 environment and
verifier images are built and pushed like all the others (`bdqnghi/terminal-bench-v3:<task>`,
`bdqnghi/terminal-bench-v4:<task>`, plus the `-verifier` tags); validation results from a GH200 would
be welcome.

Current status of the long-running items is tracked in [STATUS.md](STATUS.md).

## Host requirements

* Docker with BuildKit, Compose v2.24+ (v5 used here).
* `qemu-user-static` binfmt registered with the `F` flag (`docker run --privileged --rm tonistiigi/binfmt
  --install amd64`; on a DGX Spark this is not persistent across reboots, and the FEX handler that remains
  cannot run x86 containers). Three tasks ship x86-64 artefacts that are analysed or executed under
  emulation inside otherwise native images (see deviations).
* For GPU tasks: NVIDIA driver with CDI devices (`nvidia.com/gpu=all`), plus the Harbor patch in
  `terminal_bench/harbor-0.23.0-gpu-cdi.patch`.
* Raise Docker's default `nofile` soft limit (this host shipped 1024, which made MongoDB drop
  connections mid-test); one task image works around it, but `/etc/docker/daemon.json`
  `default-ulimits` is the proper fix.

## How to run

### SWE-bench Pro

Use the official harness with the namespace switched:

```
python swe_bench_pro_eval.py --use_local_docker --dockerhub_username bdqnghi --docker_platform linux/arm64 ...
```

`swebench_pro/validate.py` runs every instance's gold patch through the harness and was used to
validate all 731 images. `swebench_pro/build.py` is the rebuild pipeline (pinned toolchains from the
official image fingerprints in `fingerprints/`, `tag_dates.json` for dated PyPI/NodeSource installs).

### DeepSWE

```
git clone https://github.com/<datacurve>/deep-swe    # the public task mirror
cd deep-swe && git checkout 0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea
git apply ../deepswe/deep-swe-arm64.patch
pier run -p tasks --agent <agent> --env docker --no-force-build
```

The patch points every `task.toml` `docker_image` and every `tests/Dockerfile` `FROM` at the arm64
images (Harbor builds the verifier from `tests/Dockerfile`, so both must change), and carries the
arm64 fixes listed below. `drizzle-orm-window-function-builders` additionally needs
`deepswe/regen_pnpm_store.sh` if you rebuild it yourself (its lockfile pins an npm tarball that no
longer exists; the pnpm store is transplanted from the official image).

### ProgramBench

```
PROGRAMBENCH_DOCKER_ORG=bdqnghi programbench eval <run_dir> -w 4
```

The image names are otherwise unchanged. The evaluator removes the x86 `./executable` reference
binary and rebuilds the submission from source, so the arm64 images carry no reference binary and
need none. `programbench/parity_check.py` compares toolchains and installed packages against the
official images; `parity_fixups.json` lists the runtime libraries the official images obtained from a
private setup script and which were added back.

### SWE-Bench-ProMax

Use the official `test_run.py` with `--golden swebench_promax/swe-bench-promax.arm64.json`; it is
the upstream dataset with `image_name` rewritten to `bdqnghi/swebench-promax:<instance_id>`.
`build_promax.py` reconstructs each image from the official image's BuildKit history
(`tools/reconstruct.py`), pins Python packages and lockfiles to what the official image resolved,
transplants architecture-neutral caches (cargo registry, deno, Hugging Face, Maven, npm) and
validates the gold patch offline before pushing.

On hosts with Linux 6.13 or newer, run the eval containers with the classic receive-buffer ceiling
(`docker run --sysctl net.ipv4.tcp_rmem="4096 131072 6291456"`; `build_promax.py` adds it to
`test_run.py`'s container flags). Newer kernels default to 32 MB, and WasmEdge's `WasiTest.*Socket*`
poll tests, which assume the older TCP buffer dynamics, then fail intermittently on the official
amd64 image as well as on the arm64 one.

### Terminal-Bench 3.0 / 4.0

```
git clone https://github.com/harbor-framework/terminal-bench tb-v3.0.0 && git -C tb-v3.0.0 checkout v3.0.0
git apply --directory tb-v3.0.0 terminal_bench/terminal-bench-v3.0.0-arm64.patch
python terminal_bench/build_tb.py --dataset-only        # writes tasks_arm64/v3 (and v4) with docker_image set
harbor run -p "$PWD/tasks_arm64/v3" -a <agent> -e docker -n 4 -o "$PWD/jobs" --no-force-build
```

Use absolute paths for `-p` and `-o`; Harbor resolves `docker compose cp` paths against the task
directory otherwise. The dataset copies set `docker_image` both in `[environment]` and in
`[verifier.environment]`, because Harbor's separate verifier inherits the agent environment and
would otherwise run the agent image without the baked `/tests`. GPU tasks need the Harbor patch
(`harbor-0.23.0-gpu-cdi.patch`), which declares GPU support for the Docker environment and adds a
CDI device reservation to the compose override.

## How the images were built

* **Dockerfiles published** (DeepSWE, Terminal-Bench): built natively with `docker buildx`/`docker
  build --platform linux/arm64`, with minimal per-task patches where a recipe was x86-specific.
* **No Dockerfiles published** (SWE-bench Pro, ProgramBench, ProMax): the BuildKit history of the
  official image records every instruction. `tools/reconstruct.py` turns it back into a Dockerfile,
  drops the unrecoverable `COPY`/`ADD` layers and re-adds their content from the official image where
  it is architecture-neutral (workspaces, lockfiles, package caches). The base image is detected from
  `/etc/os-release` and the image's `ENV` (`PYTHON_VERSION`, `GOLANG_VERSION`, `JAVA_VERSION`,
  `MAVEN_VERSION`, istio labels, ...).
* **Drift control**: recipes re-resolve dependencies at build time, so a rebuild months later gets
  newer packages and hidden tests fail. Installed Python distributions of the official image become a
  `PIP_CONSTRAINT`/`UV_CONSTRAINT` file (build backends excluded), Cargo/npm/pnpm/poetry lockfiles are
  restored before the install steps, and toolchains are pinned to the official versions.

## Deviations from the official images

Every deviation is the minimum change needed for arm64 and is recorded in the patch files.

* **Emulated pieces** (host binfmt): `ico-path-patch` ships an x86-64 service binary (x86 libc added
  via Debian multiarch); `memcached-backdoor` analyses an x86-64 target with Ghidra, whose arm64
  natives are built from the bundled sources; DeepSWE `scriggo` runs its Go test binaries as amd64
  under qemu because two tests encode amd64 float rounding (`math.Log` is assembly on amd64 only).
* **Package versions without arm64 builds**: pymeep 1.29 -> 1.30.1 (wdm-design), cascadio 0.0.17 ->
  0.1.1 (cad-model verifier), build123d 0.10.0 -> current (cad-model oracle), PyQt5 from source /
  distro (SWE-bench Pro qutebrowser), MongoDB from the Ubuntu arm64 repo (DeepSWE eicrud).
* **Tools that pin x86 downloads**: cargo-nextest, deno, Go toolchain tarballs, NodeSource, Arm GCC
  (betaflight `mk/tools.mk`), JDK paths (`java-*-openjdk-amd64`), `GOARCH=amd64`.
* **Upstream rot fixed in passing**: Debian bullseye/buster archives moved to `archive.debian.org`,
  a renamed Hugging Face dataset (layout-config-recreation2), Chromium 136+ needing
  `--user-data-dir`/`--ozone-platform=x11` for remote debugging (medical-claims sidecar), a removed
  npm tarball (drizzle-orm).
* **Arch-sensitive compiler defaults**: ETL tests need `-fsigned-char` on arm64 (char is unsigned);
  `vpp-loss-divergence` pins the CPU torch build the task intended (pip otherwise swaps in CUDA
  torch on aarch64).

## Repository layout

```
tools/reconstruct.py            history -> Dockerfile reconstruction (generic)
swebench_pro/                   build.py, validate.py, fingerprints of official images, tag dates
deepswe/                        build_deepswe.py, pin_task.py, deep-swe-arm64.patch, regen_pnpm_store.sh
programbench/                   build_programbench.py, parity_check.py, parity_fixups.json, gold examples
swebench_promax/                build_promax.py, swe-bench-promax.arm64.json
terminal_bench/                 build_tb.py, v3/v4 patches, harbor GPU patch
STATUS.md                       per-benchmark validation status and known failures
```

Scripts are MIT licensed (see LICENSE). The patches modify files from the respective upstream
benchmark repositories and keep their licenses.
