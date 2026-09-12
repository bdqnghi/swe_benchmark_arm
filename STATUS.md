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

## Terminal-Bench 3.0 — 70/74 validated, 4 GPU tasks in progress

All 74 environment and 74 verifier images built and pushed. Harbor oracle passes on all 70 non-GPU
tasks, including memcached-backdoor (x86-only upstream; arm64 Ghidra natives built from the bundled
sources) and ico-path-patch (x86 service binary under qemu with multiarch libc). The 4 GPU tasks
(fp8-rmsnorm-gemm, jax-speedrun-gpu, exam-pdf-eval, math-eval-grader) are being validated on the GB10
through the Harbor CDI patch.

## Terminal-Bench 4.0 — 63/66 validated, 3 GPU tasks in progress

All 66 environment and 66 verifier images built and pushed. Harbor oracle passes on all 63 non-GPU
tasks (live-database-cutover is load-sensitive: it failed while the host was saturated and passed
when run alone). GPU tasks fp8-rmsnorm-gemm, jax-speedrun-gpu, math-eval-grader follow the v3 run.
