#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
engine="${1:-podman}"
case "$engine" in podman|docker) ;; *) echo 'Choose podman or docker' >&2; exit 2 ;; esac
test -f .env || { echo 'Create .env as described in README.md first' >&2; exit 2; }
project="sigma-smoke-$(date +%s)-$$"
image="localhost/$project:local"
temporary="$(mktemp -d)"
export SIGMA_PORT="${SIGMA_SMOKE_PORT:-18001}"
printf '{"services":{"kernel":{"image":"%s","restart":"no"}}}\n' "$image" > "$temporary/override.json"
compose=("$engine" compose -p "$project" -f compose.yaml -f "$temporary/override.json")
cleanup() {
    status=$?
    trap - EXIT
    if [ "$status" -ne 0 ]; then "${compose[@]}" logs --tail=40 kernel || true; fi
    # Only resources belonging to this uniquely named, disposable test project.
    "${compose[@]}" down --volumes || true
    "$engine" image rm "$image" >/dev/null 2>&1 || true
    rm -f "$temporary/override.json"
    rmdir "$temporary"
    exit "$status"
}
trap cleanup EXIT
"$engine" build -t "$image" .
"${compose[@]}" up -d --no-build --wait --wait-timeout 60
curl --fail --silent --show-error "http://127.0.0.1:$SIGMA_PORT/health"
"${compose[@]}" exec -T kernel python - prepare < tests/container_smoke.py
for phase in assets finish; do
    "${compose[@]}" kill -s SIGKILL kernel
    "${compose[@]}" up -d --no-build --force-recreate --wait --wait-timeout 60
    "${compose[@]}" exec -T kernel python - "$phase" < tests/container_smoke.py
done
echo 'PASS: container HTTP scenarios and two forced-kill recoveries'
