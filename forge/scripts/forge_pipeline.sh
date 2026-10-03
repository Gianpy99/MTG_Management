#!/usr/bin/env bash
# =============================================================================
# forge/scripts/forge_pipeline.sh - CI/CD steps for the mtg-forge container.
# Called by the Jenkinsfile from the repository root (Jenkins talks to the Pi's
# Docker daemon through the mounted socket):
#
#   forge_pipeline.sh unit         unit tests (docker build --target test)
#   forge_pipeline.sh build        build mtg-forge:candidate from forge/forge.version
#   forge_pipeline.sh integration  real Forge game inside the candidate image
#   forge_pipeline.sh deploy       swap container, health check + real Forge game in
#                                  the deployed container, automatic rollback
#   forge_pipeline.sh rollback     manual: restart the container from mtg-forge:previous
#
# - Unchanged image + unchanged run configuration => no restart (queued/running
#   simulations are not interrupted).
# - A candidate that failed integration/deploy is tagged mtg-forge:failed and is not
#   retried until its content changes (FORGE_FORCE_DEPLOY=true overrides both rules).
# =============================================================================
set -euo pipefail

CONTAINER=${FORGE_CONTAINER:-mtg-forge}
IMAGE=${FORGE_IMAGE:-mtg-forge}
NETWORK=${MTG_NETWORK:-mtg-net}
VOLUME=${FORGE_DATA_VOL:-mtg-forge-data}
HOST_PORT=${FORGE_HOST_PORT:-8787}
FORCE=${FORGE_FORCE_DEPLOY:-false}
DECISION_FILE=.forge-decision
TEST_JAVA_OPTS="-Xmx1536m -Dfile.encoding=UTF-8 -Dio.netty.tryReflectionSetAccessible=true"
# Any change to this script (and so to how the container is run) forces a redeploy.
RUN_HASH=$( { cat "$0"; echo "$NETWORK $VOLUME $HOST_PORT"; } | sha256sum | cut -c1-16)

ver() { sed -n "s/^$1=//p" forge/forge.version | tr -d '\r\n '; }
say() { echo; echo "=== [mtg-forge] $* ==="; }
decision() { cat "$DECISION_FILE" 2>/dev/null || echo missing; }
image_id() { docker image inspect -f '{{.Id}}' "$1" 2>/dev/null || true; }

run_container() {
    docker network inspect "$NETWORK" >/dev/null 2>&1 || docker network create "$NETWORK" >/dev/null
    docker volume create "$VOLUME" >/dev/null
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    docker run -d \
        --name "$CONTAINER" \
        --label "mtg-forge.run-hash=$RUN_HASH" \
        --restart unless-stopped \
        --network "$NETWORK" --network-alias "$CONTAINER" \
        -p "$HOST_PORT:8787" \
        -v "$VOLUME:/data" \
        "$1" >/dev/null
}

wait_healthy() {
    local deadline=$((SECONDS + ${1:-180}))
    while [ $SECONDS -lt $deadline ]; do
        if docker exec "$CONTAINER" python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=6).status==200 else 1)" 2>/dev/null; then
            return 0
        fi
        sleep 3
    done
    return 1
}

mark_failed() {
    docker tag "$IMAGE:candidate" "$IMAGE:failed"
    echo failed > "$DECISION_FILE"
    echo "Candidate tagged $IMAGE:failed: it will not be redeployed until forge/ changes." >&2
}

case "${1:-}" in
unit)
    say "Unit tests"
    docker build -f forge/Dockerfile --target test -t "$IMAGE:test" forge
    ;;

build)
    rm -f "$DECISION_FILE"
    FORGE_VERSION=$(ver FORGE_VERSION)
    FORGE_SHA256=$(ver FORGE_SHA256)
    if [ -z "$FORGE_VERSION" ] || [ -z "$FORGE_SHA256" ]; then
        echo "forge/forge.version must define FORGE_VERSION and FORGE_SHA256" >&2
        exit 1
    fi
    say "Build image (Forge $FORGE_VERSION)"
    docker build -f forge/Dockerfile --target runtime \
        --build-arg FORGE_VERSION="$FORGE_VERSION" \
        --build-arg FORGE_SHA256="$FORGE_SHA256" \
        -t "$IMAGE:candidate" forge
    new_id=$(image_id "$IMAGE:candidate")
    failed_id=$(image_id "$IMAGE:failed")
    running_id=$(docker inspect -f '{{.Image}}' "$CONTAINER" 2>/dev/null || true)
    running=$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo false)
    running_hash=$(docker inspect -f '{{index .Config.Labels "mtg-forge.run-hash"}}' "$CONTAINER" 2>/dev/null || true)
    if [ "$FORCE" = true ]; then
        echo deploy > "$DECISION_FILE"
        echo "FORGE_FORCE_DEPLOY=true: $new_id will be tested and deployed."
    elif [ "$new_id" = "$failed_id" ]; then
        echo known-bad > "$DECISION_FILE"
        echo "Candidate $new_id already failed before: not deploying it again." >&2
    elif [ "$new_id" = "$running_id" ] && [ "$running" = true ] && [ "$running_hash" = "$RUN_HASH" ]; then
        echo skip > "$DECISION_FILE"
        echo "Image and run configuration unchanged: the running container is kept."
    else
        echo deploy > "$DECISION_FILE"
        echo "New image/configuration ($new_id) will be tested and deployed."
    fi
    ;;

integration)
    case "$(decision)" in
    skip) say "Integration test skipped (nothing changed)"; exit 0 ;;
    deploy) ;;
    known-bad) echo "Known failing candidate: set FORGE_FORCE_DEPLOY=true to retry." >&2; exit 1 ;;
    *) echo "No build decision ($(decision)): build step did not complete." >&2; exit 1 ;;
    esac
    say "Forge integration test (Java >= 17, card DB, real Forge CLI game)"
    if ! docker run --rm --name "$CONTAINER-selftest" -e FORGE_JAVA_OPTS="$TEST_JAVA_OPTS" \
            "$IMAGE:candidate" python -m forge_engine selftest; then
        mark_failed
        exit 1
    fi
    ;;

deploy)
    case "$(decision)" in
    skip)
        say "Deploy skipped (nothing changed) - health check only"
        docker network inspect "$NETWORK" >/dev/null 2>&1 || docker network create "$NETWORK" >/dev/null
        docker network connect --alias "$CONTAINER" "$NETWORK" "$CONTAINER" 2>/dev/null || true
        wait_healthy 120
        exit 0
        ;;
    deploy) ;;
    *) echo "Not deploying (decision: $(decision))." >&2; exit 1 ;;
    esac
    say "Deploy"
    previous_id=$(docker inspect -f '{{.Image}}' "$CONTAINER" 2>/dev/null || true)
    if [ -n "$previous_id" ] && [ "$previous_id" != "$(image_id "$IMAGE:candidate")" ]; then
        docker tag "$previous_id" "$IMAGE:previous"
        echo "Previous image saved as $IMAGE:previous ($previous_id)"
    fi
    docker tag "$IMAGE:candidate" "$IMAGE:latest"
    run_container "$IMAGE:latest"

    # The post-deploy game runs in the deployed container but outside the job queue, so
    # it never waits behind (or disturbs) user simulations re-queued by the restart.
    say "Health check + post-deployment Forge game"
    if wait_healthy 180 && docker exec -e FORGE_JAVA_OPTS="$TEST_JAVA_OPTS" "$CONTAINER" python -m forge_engine selftest; then
        docker image prune -f --filter "label=app=mtg-forge" >/dev/null || true
        echo "mtg-forge deployed: http://192.168.1.129:$HOST_PORT/api/forge/status"
        exit 0
    fi
    echo "Deployment FAILED - container log:" >&2
    docker logs --tail 80 "$CONTAINER" >&2 || true
    mark_failed
    if docker image inspect "$IMAGE:previous" >/dev/null 2>&1; then
        say "Rolling back to $IMAGE:previous"
        run_container "$IMAGE:previous"
        if wait_healthy 180; then echo "Rollback healthy."; else echo "Rollback container is NOT healthy." >&2; fi
    else
        echo "No previous image to roll back to." >&2
    fi
    exit 1
    ;;

rollback)
    docker image inspect "$IMAGE:previous" >/dev/null
    say "Manual rollback to $IMAGE:previous"
    run_container "$IMAGE:previous"
    wait_healthy 180
    ;;

*)
    sed -n '2,20p' "$0"
    exit 2
    ;;
esac
