#!/usr/bin/env bash
set -euo pipefail

image="${1:?Candidate ULM image needed}"
network="ulm-core-$$"
core="ulm-ha-$$"
addon="ulm-app-$$"
work="$(mktemp -d)"
export HA_LLM_TOKEN
HA_LLM_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"

cleanup() {
  docker rm -f "$core" "$addon" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  # The Core container created some config files as root.
  docker run --rm --network none --mount "type=bind,source=$work,target=/fixture"     --entrypoint python "$image" -c 'import pathlib, shutil; [shutil.rmtree(p) if p.is_dir() else p.unlink() for p in pathlib.Path("/fixture").iterdir()]'     >/dev/null 2>&1 || true
  rmdir "$work" 2>/dev/null || true
}
trap cleanup EXIT
docker network create --subnet 172.30.32.0/23 "$network" >/dev/null
mkdir -p "$work/custom_components"
cp -r custom_components/universal_listings_manager "$work/custom_components/"
cat > "$work/configuration.yaml" <<'YAML'
homeassistant:
  time_zone: UTC
http:
api:
config:
onboarding:
person:
frontend:
diagnostics:
YAML

docker run -d --name "$addon" --network "$network" --ip 172.30.32.11   --network-alias 6a2752e9-olx-manager   -e HA_LLM_TOKEN -e OWNER_USER_ID=fixture-owner -e OLX_WRITE_ENABLED=false -e MCP_ENABLED=false   "$image" >/dev/null

# Use the real official Home Assistant Core server. The port belongs to this
# isolated acceptance test and is accessible only from the runner's loopback.
docker run -d --name "$core" --network "$network" --ip 172.30.32.12 -p 127.0.0.1::8123   --mount "type=bind,source=$work,target=/config"   ghcr.io/home-assistant/home-assistant:2026.11.0 >/dev/null
addr="$(docker port "$core" 8123/tcp)"
if ! python scripts/ha_mcp_acceptance.py "http://$addr" "$core" "$network" "$image"; then
  docker logs --tail 90 "$core"
  docker logs --tail 70 "$addon"
  exit 1
fi
