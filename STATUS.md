# Validation status

Last updated: 2026-09-12 (in progress items are marked).

## SWE-bench Pro — complete

731/731 instance images and 69 base images built natively; all 731 gold patches pass with the
official harness (`swebench_pro/validate.py`). One dataset quirk (`vuls`, a test-name quoting
mismatch) fails identically on the official amd64 image.

## DeepSWE — complete

113/113 tasks have arm64 images; the Pier/Harbor oracle (reference solution) passes on all 113
with the patched task tree (`deepswe/deep-swe-arm64.patch`). Fixes applied: pinned Python packages
to the official image's versions for skrub, narwhals, dateutil, numba (`pin_task.py`); eicrud wraps
`mongod` to raise the file-descriptor soft limit; scriggo's verifier runs amd64 test binaries under
qemu; drizzle-orm seeds pnpm's store from the official image; nextest/deno/MongoDB download fixes.

## ProgramBench — complete

200/200 images built and pushed. Toolchain parity with the official images verified on all 200
(`parity_check.py`): go, rustc, cargo, gcc, cmake, python and pip packages identical; apt differs
only by Ubuntu point releases. Seven images were missing runtime libraries that the official build
installed from a private setup script (Java for ditaa; cairo/pango/rsvg, libgeotiff/proj, libltdl,
libjansson/libyaml, ripgrep for six others) and were fixed (`parity_fixups.json`). End-to-end
`programbench eval` with gold submissions passes on arm64 (cmatrix solved; quickjs 3035/3036, the one
failure being a directory-order-dependent test).

## SWE-Bench-ProMax — in progress

Each image is gold-validated with the official `test_run.py` (offline) before being pushed.
Progress and the per-repository arm64 fixes are tracked by `build_promax.py`:

* maven/eclipse-temurin/istio base detection, `openjdk-amd64` -> `openjdk-arm64`, `GOARCH`, Go
  toolchain tarballs, `TARGET_ARCH`
* ETL: `-fsigned-char`; betaflight: aarch64 entry in `mk/tools.mk`; WasmEdge: apt.llvm.org pinned out
  on arm64; EOL Debian archives
* constraints that clash with a repo's own exact pins are dropped automatically and the build retried
* eval scripts that hard-code `/usr/lib/jvm/java-N-openjdk-amd64` get a symlink to the arm64 JVM;
  baked wheelhouses (`/opt/promax-wheelhouse/<id>`) and Bazel's repository caches (default and the
  `--config=ci-linux` one under `/var/lib/buildkite-agent`) are regenerated/transplanted; builds retry on
  network-error signatures
* bazel: `third_party/BUILD` strips every `.so` from the netty-tcnative jar on linux_aarch64 (a stale
  "the .so is x86" rule), so the `linux-aarch_64` native of the exact version Bazel resolved is placed on
  `java.library.path`, where Netty's loader finds it
* fprime: `Fw/Logger/test/ut/LoggerRules.cpp` (at the instances' base commits) passes `U32` values through
  `%lu`; that is undefined behaviour which passes on x86-64 (verified 5/5 on the official image under qemu)
  and fails on every aarch64 run because stack-passed varargs keep stale upper halves. The image applies
  upstream's own later fix (nasa/fprime 589ed5d, "Switch to U64 Logger Tests", #4262).
* timing-sensitive gold tests (fprime `PosixRawTimeTest` 25 us threshold, bazel
  `RemoteExecutionServiceTest` interrupt race) fail only while the host is saturated by parallel builds
  and pass when validated alone
* WasmEdge `WasiTest.*Socket*`: the poll tests write until EAGAIN, expect a 100 ms timeout, have the
  peer drain, then expect writability; whether that holds depends on the kernel's TCP buffer
  autotuning. Under Linux 6.17's 32 MB `tcp_rmem` ceiling they fail intermittently on the official
  amd64 image too (0/6 clean runs under qemu on this host); with the pre-6.13 ceiling
  (`--sysctl net.ipv4.tcp_rmem="4096 131072 6291456"`) they pass 6/6. The validation runs eval
  containers with that setting (see README).

## Terminal-Bench 3.0 — 72/74 validated; 2 tasks need H100 hardware

All 74 environment and 74 verifier images built and pushed. Harbor oracle passes on all 70 non-GPU
tasks, including coq-block-bound (native Coq base, see below), memcached-backdoor (upstream pins linux/amd64 for Ghidra; the arm64 image builds
Ghidra's native decompiler/sleigh/demangler from the bundled sources) and ico-path-patch (x86 service
binary under qemu with multiarch libc). GPU tasks, validated through the Harbor CDI patch on a GB10:

* exam-pdf-eval: passes (20/20) with torch 2.9.1+cu130 (torch 2.3.1 has no arm64 CUDA wheel).
* math-eval-grader: rebuild in progress on a multi-arch base (`pytorch/pytorch:2.3.1-cuda12.1` is
  amd64-only; the earlier "arm64" build had silently run under emulation).
* fp8-rmsnorm-gemm and jax-speedrun-gpu: images built and pushed, but both tasks declare
  `gpu_types = ["H100"]` and cannot pass on other hardware: the reference fp8 kernel uses Hopper-only
  `wgmma` instructions, and the speedrun grader enforces an H100-calibrated wall-clock budget that the
  GB10 exceeds. They need an arm64 Hopper host (e.g. GH200) for validation.

coq-block-bound passes (v3 and v4) on `bdqnghi/coq:8.18`, a multi-arch manifest joining the official
amd64 `coqorg/coq:8.18` with a native arm64 build of the same layout (`terminal_bench/bases/coq-8.18`,
Coq 8.18.0 on OCaml 4.13.1+flambda), because the official image is amd64-only.

## Terminal-Bench 4.0 — 64/66 validated; math-eval-grader rebuild in progress

All 66 environment and 66 verifier images built and pushed. Harbor oracle passes on all 63 non-GPU
tasks (live-database-cutover is load-sensitive: it failed while the host was saturated and passed
when run alone). fp8-rmsnorm-gemm and jax-speedrun-gpu need H100 hardware (see 3.0);
coq-block-bound passes on the multi-arch Coq base; math-eval-grader follows the v3 rebuild.
