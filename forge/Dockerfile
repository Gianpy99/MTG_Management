# =============================================================================
# forge/Dockerfile - MTG Forge Engine (Forge CLI + Java 17 + job-queue API)
# -----------------------------------------------------------------------------
# Build context: the forge/ folder.
#   docker build -f forge/Dockerfile --build-arg FORGE_VERSION=... \
#                --build-arg FORGE_SHA256=... -t mtg-forge forge
#   docker build -f forge/Dockerfile --target test forge   # unit tests only
# =============================================================================
ARG FORGE_VERSION=2.0.15
ARG FORGE_SHA256=4468f182b60f6ca62d89697baa208981085af744f116781af751c611826db600

# --- Forge distribution (download + checksum + trim GUI-only assets) --------
FROM debian:bookworm-slim AS forge-dist
ARG FORGE_VERSION
ARG FORGE_SHA256
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl bzip2 \
 && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    curl -fsSL -o /tmp/forge.tar.bz2 \
      "https://github.com/Card-Forge/forge/releases/download/forge-${FORGE_VERSION}/forge-installer-${FORGE_VERSION}.tar.bz2"; \
    echo "${FORGE_SHA256}  /tmp/forge.tar.bz2" | sha256sum -c -; \
    mkdir -p /opt/forge; \
    tar -xjf /tmp/forge.tar.bz2 -C /opt/forge; \
    rm /tmp/forge.tar.bz2; \
    cd /opt/forge; \
    test -f "forge-gui-desktop-${FORGE_VERSION}-jar-with-dependencies.jar"; \
    ln -s "forge-gui-desktop-${FORGE_VERSION}-jar-with-dependencies.jar" forge.jar; \
    rm -rf docs res/adventure res/music res/sound *.exe *.cmd *.command \
           adventure-editor* forge-adventure* gdx-particle-editor.jar forge-gui-mobile-dev-*.jar

# --- Python + Java runtime --------------------------------------------------
FROM python:3.12-slim-bookworm AS base
LABEL app=mtg-forge
RUN mkdir -p /usr/share/man/man1 \
 && apt-get update \
 && apt-get install -y --no-install-recommends openjdk-17-jre-headless \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY service/forge_engine ./forge_engine
ENV PYTHONUNBUFFERED=1

# --- Unit tests (no Forge download needed) ----------------------------------
FROM base AS test
COPY service/requirements-dev.txt ./requirements-dev.txt
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY service/tests ./tests
RUN python -m pytest -q -p no:cacheprovider tests

# --- Production image -------------------------------------------------------
FROM base AS runtime
ARG FORGE_VERSION
COPY --from=forge-dist /opt/forge /opt/forge
COPY tolkien_forge_decks_fixed ./test_decks
RUN useradd --uid 10001 --create-home forge \
 && mkdir -p /data \
 && chown forge:forge /data
ENV FORGE_HOME=/opt/forge \
    FORGE_VERSION=${FORGE_VERSION} \
    FORGE_DATA_DIR=/data \
    FORGE_TEST_DECKS_DIR=/srv/test_decks \
    FORGE_JAVA_OPTS="-Xmx2g -Dfile.encoding=UTF-8 -Dio.netty.tryReflectionSetAccessible=true"
USER forge
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=8s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=6).status==200 else 1)"
CMD ["uvicorn", "forge_engine.api:app", "--host", "0.0.0.0", "--port", "8787"]
