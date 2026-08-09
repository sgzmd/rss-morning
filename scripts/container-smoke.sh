#!/bin/sh
set -eu

image=${1:?usage: scripts/container-smoke.sh IMAGE}

docker run --rm --network none "$image" --help
docker run --rm --network none --entrypoint python "$image" scripts/container_smoke.py
docker image inspect "$image" --format 'image_size_bytes={{.Size}}'
docker run --rm --network none --entrypoint python "$image" -m pip list --format=freeze
docker history --no-trunc "$image"
