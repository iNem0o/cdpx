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
  container|inspect) exit 1 ;;
  *) exit 0 ;;
esac
EOF
chmod 0755 "$temporary/bin/docker"

output=$(PATH="$temporary/bin:$PATH" CDPX_IMAGE_REF=example.invalid/cdpx@sha256:test \
    "$root/cdpx" --version)
printf '%s\n' "$output" | grep '"launcher_version":"0.1.0"' >/dev/null
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
