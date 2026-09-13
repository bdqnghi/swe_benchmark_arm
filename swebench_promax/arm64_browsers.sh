#!/bin/bash
# rules_browsers (angular/dev-infra) ships Chrome/Firefox only for linux-x86_64, macOS and Windows, so on linux-arm64 its
# browser_group select() has no matching branch and any web test target fails analysis. Add a linux_arm64 branch to the
# fetched copy of the rules and point the browser repositories at Debian's arm64 chromium, chromedriver and firefox-esr
# via Bazel repository overrides (.bazelrc.user is try-imported by the workspace's .bazelrc). No-op when the workspace
# does not use rules_browsers.
set -euo pipefail
[ "$(uname -m)" = "aarch64" ] || exit 0
OB=""; for c in /root/.cache/bazel/_bazel_root/*/external; do [ -d "$c" ] && OB=$c; done
[ -n "$OB" ] || { echo "arm64_browsers: no bazel output base"; exit 0; }
RB=""; for c in "$OB"/rules_browsers~ "$OB"/rules_browsers+; do [ -d "$c" ] && RB=$c; done
[ -n "$RB" ] || { echo "arm64_browsers: rules_browsers not used"; exit 0; }
SEP="${RB: -1}"   # canonical-name separator: '~' (Bazel 7) or '+' (Bazel 8)
echo "arm64_browsers: rules_browsers at $RB"
export DEBIAN_FRONTEND=noninteractive
apt-get update && apt-get install -y --no-install-recommends chromium chromium-driver firefox-esr && rm -rf /var/lib/apt/lists/*
grep -q linux_arm64 "$RB/browsers/private/BUILD.bazel" || printf '\nconfig_setting(\n    name = "linux_arm64",\n    constraint_values = ["@platforms//os:linux", "@platforms//cpu:aarch64"],\n)\n' >> "$RB/browsers/private/BUILD.bazel"
python3 - "$RB" <<'PY'
import sys, pathlib
rb = pathlib.Path(sys.argv[1])
for name, repos in (("chromium", ["rules_browsers_chrome_linux", "rules_browsers_chromedriver_linux"]), ("firefox", ["rules_browsers_firefox_linux"])):
    p = rb / "browsers" / name / "BUILD.bazel"
    if not p.exists():
        continue
    s = p.read_text()
    if "linux_arm64" in s or '"//browsers/private:linux_x64": [' not in s:
        continue
    branch = '"//browsers/private:linux_arm64": [\n' + "".join(f'            "@{r}//:info",\n' for r in repos) + '        ],\n        "//browsers/private:linux_x64": ['
    p.write_text(s.replace('"//browsers/private:linux_x64": [', branch, 1))
    print("arm64_browsers: patched", p)
PY
mk() {  # mk <repo dir> <relative binary path> <NAMED FILE> <wrapper command>
  local d=/opt/rb-arm64/$1 rel=$2 nf=$3 cmd=$4
  mkdir -p "$d/$(dirname "$rel")"
  printf '#!/bin/sh\nexec %s "$@"\n' "$cmd" > "$d/$rel"; chmod +x "$d/$rel"
  printf 'load("@rules_browsers//browsers/private:browser_artifact.bzl", "browser_artifact")\n\nbrowser_artifact(\n  name = "info",\n  files = glob(["**/*"]),\n  named_files = {":%s": "%s"},\n  visibility = ["//visibility:public"],\n)\n\nexports_files(["%s"])\n' "$rel" "$nf" "$rel" > "$d/BUILD.bazel"
  touch "$d/WORKSPACE" "$d/REPO.bazel"
}
mk chrome_linux chrome-headless-shell-linux64/chrome-headless-shell CHROME-HEADLESS-SHELL "/usr/bin/chromium --headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage"
mk chromedriver_linux chromedriver-linux64/chromedriver CHROMEDRIVER "/usr/bin/chromedriver"
mk firefox_linux firefox/firefox FIREFOX "/usr/bin/firefox-esr"
for r in chrome_linux chromedriver_linux firefox_linux; do
  echo "common --override_repository=rules_browsers${SEP}${SEP}browsers${SEP}rules_browsers_${r}=/opt/rb-arm64/${r}" >> /testbed/.bazelrc.user
done
echo "arm64_browsers: overrides written to /testbed/.bazelrc.user"
