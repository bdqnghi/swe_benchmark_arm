# netdata: the official image carries a CMake/Ninja build of the repository at the base commit in /opt/netdata-build
# and its installation (/usr/sbin/netdata, plugins under /usr/libexec/netdata ...). The RF run_script runs the C unit
# tests with the installed binary (`/usr/sbin/netdata -W unittest`). Those binaries are x86-64 and were dropped from
# the transplant; rebuild the same tree natively with the options recorded in the official CMakeCache.txt, then install.
B=/opt/netdata-build
C="$B/CMakeCache.txt"
[ -f "$C" ] || { echo "no $C, nothing to rebuild"; exit 0; }
SRC=$(sed -n 's/^CMAKE_HOME_DIRECTORY:INTERNAL=//p' "$C")
GEN=$(sed -n 's/^CMAKE_GENERATOR:INTERNAL=//p' "$C")
OPTS=$(grep -E '^(CMAKE_BUILD_TYPE|CMAKE_INSTALL_PREFIX|DEFAULT_FEATURE_STATE|ENABLE_[A-Z0-9_]+|WITH_[A-Z0-9_]+|BUILD_[A-Z0-9_]+|NETDATA_[A-Z0-9_]+):[A-Z]+=' "$C" \
       | sed -E 's/^([^:]+):[A-Z]+=(.*)$/-D\1=\2/' | tr '\n' ' ')
echo "reconfiguring $SRC -> $B with: $OPTS"
mv "$B" "$B.amd64"
cmake -S "$SRC" -B "$B" ${GEN:+-G "$GEN"} $OPTS
cmake --build "$B" -j"$(nproc)"
cmake --install "$B"
rm -rf "$B.amd64"
test -x /usr/sbin/netdata && /usr/sbin/netdata -W buildinfo | head -5 || true
