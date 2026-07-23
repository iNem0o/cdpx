#!/bin/sh
set -eu

root=$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd -P)
temporary=$(mktemp -d "${TMPDIR:-/tmp}/cdpx-launcher-test.XXXXXX")
trap 'rm -rf "$temporary"' EXIT HUP INT TERM

mkdir -p "$temporary/bin"
cat > "$temporary/bin/docker" <<'EOF'
#!/bin/sh
case "$1" in
  info) exit 0 ;;
  container|inspect)
    # CDPX_STUB_RUNNING simulates a live runtime container so the
    # docker exec compile path is selected instead of docker run.
    if [ "${CDPX_STUB_RUNNING:-no}" = "yes" ]; then
      [ "$1" = "inspect" ] && printf 'true\n'
      exit 0
    fi
    exit 1
    ;;
esac
compile=no
for argument in "$@"; do
  [ "$argument" = "cdpx.runtime_config" ] && compile=yes
done
if [ "$compile" = "yes" ] && [ -n "${CDPX_TEST_LOG:-}" ]; then
  previous=""
  output=""
  for argument in "$@"; do
    printf '%s\n' "$argument" >> "$CDPX_TEST_LOG"
    [ "$previous" = "--output" ] && output=$argument
    previous=$argument
  done
  mkdir -p "$output"
  printf 'stub-fingerprint\n' > "$output/fingerprint"
  printf '86400\n' > "$output/idle-timeout"
  : > "$output/docker.args"
  : > "$output/environment.required"
  : > "$output/environment.optional"
  : > "$output/environment.set"
  printf '{"schema":"cdpx.runtime-plan/v1","stub":true}\n' > "$output/plan.json"
fi
exit 0
EOF
chmod 0755 "$temporary/bin/docker"

output=$(PATH="$temporary/bin:$PATH" CDPX_IMAGE_REF=example.invalid/cdpx@sha256:test \
    "$root/cdpx" --version)
printf '%s\n' "$output" | grep '"launcher_version":"0.1.4"' >/dev/null
printf '%s\n' "$output" | grep '"image":"example.invalid/cdpx@sha256:test"' >/dev/null

# The source launcher refuses to run without a baked digest.
unreleased_status=0
PATH="$temporary/bin:$PATH" "$root/cdpx" --version >/dev/null 2>&1 || unreleased_status=$?
test "$unreleased_status" -eq 2

# Bake a digest exactly as the release workflow does; the released launcher
# must resolve the pinned image, and the unreleased guard must stay intact.
digest="0000000000000000000000000000000000000000000000000000000000000000"
sed "/^DEFAULT_IMAGE=/s/__CDPX_IMAGE_DIGEST__/$digest/" "$root/cdpx" > "$temporary/cdpx-released"
chmod 0755 "$temporary/cdpx-released"
output=$(PATH="$temporary/bin:$PATH" "$temporary/cdpx-released" --version)
printf '%s\n' "$output" | grep "\"image\":\"ghcr.io/inem0o/cdpx@sha256:$digest\"" >/dev/null

# Environment variables referenced by cdpx.yaml are forwarded to the
# in-image compiler; unreferenced or unset names never are.
mkdir -p "$temporary/workspace"
cat > "$temporary/workspace/cdpx.yaml" <<'EOF'
schema: cdpx/v1
# ${CDPX_TEST_UNSET} must stay unforwarded while it is absent from the host.
runtime:
  network: "network:${CDPX_TEST_NET}"
environment:
  set:
    MODE: "literal $$ dollar"
EOF
compile_log="$temporary/compile-args"
(
    cd "$temporary/workspace"
    PATH="$temporary/bin:$PATH" \
    CDPX_IMAGE_REF=example.invalid/cdpx@sha256:test \
    CDPX_TEST_LOG="$compile_log" \
    CDPX_TEST_NET=stacknet \
    "$root/cdpx" runtime plan >/dev/null
)
grep -x 'run' "$compile_log" >/dev/null
grep -A1 -x -e '--env' "$compile_log" | grep -x 'CDPX_TEST_NET=stacknet' >/dev/null
grep 'CDPX_TEST_UNSET' "$compile_log" >/dev/null && exit 1

# The same forwarding reaches the in-image compiler on the docker exec
# compile path when the runtime container is already running.
exec_log="$temporary/compile-args-exec"
(
    cd "$temporary/workspace"
    PATH="$temporary/bin:$PATH" \
    CDPX_IMAGE_REF=example.invalid/cdpx@sha256:test \
    CDPX_TEST_LOG="$exec_log" \
    CDPX_STUB_RUNNING=yes \
    CDPX_TEST_NET=stacknet \
    "$root/cdpx" runtime plan >/dev/null
)
grep -x 'exec' "$exec_log" >/dev/null
grep -A1 -x -e '--env' "$exec_log" | grep -x 'CDPX_TEST_NET=stacknet' >/dev/null
! grep 'CDPX_TEST_UNSET' "$exec_log" >/dev/null
