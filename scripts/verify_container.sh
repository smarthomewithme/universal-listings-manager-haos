#!/usr/bin/env bash
set -euo pipefail

image="${1:?Wymagany obraz kandydata}"
version="${2:?Wymagana wersja kandydata}"
previous="${3:-}"
previous_version="${4:-}"
container="ulm-verify-$$"
volume="ulm-verify-$$"
export MCP_BEARER_TOKEN
MCP_BEARER_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(36))')"

cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT

start() {
  docker run -d --name "$container" --network none     --mount "type=volume,source=$volume,target=/data"     -e DEV_PANEL=true -e MCP_ENABLED=true -e MCP_BEARER_TOKEN     -e MOCK_ENABLED=true -e OLX_WRITE_ENABLED=false "$1" >/dev/null
  for _ in {1..30}; do
    local status
    status="$(docker inspect -f '{{.State.Health.Status}}' "$container")"
    if [[ "$status" == healthy ]]; then return; fi
    if [[ "$status" == unhealthy ]]; then break; fi
    sleep 2
  done
  docker logs "$container"
  return 1
}

flow() {
  docker exec -i "$container" python - "$1" "$2" < scripts/docker_container_flow.py
}

docker volume create "$volume" >/dev/null
if [[ -n "$previous" ]]; then
  start "$previous"
  flow seed "$previous_version"
  docker rm -f "$container" >/dev/null
  start "$image"
  flow verify "$version"
else
  start "$image"
  flow seed "$version"
fi

docker exec -i -e "ULM_EXPECTED_VERSION=$version" "$container" python - < scripts/docker_smoke.py
docker rm -f "$container" >/dev/null
start "$image"
flow verify "$version"
