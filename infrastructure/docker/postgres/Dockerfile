# PostgreSQL 16 + PostGIS 3, multi-architecture.
# The upstream postgis/postgis image is published for amd64 only; this builds the same stack on the
# official multi-arch postgres image using the PostgreSQL project's Debian packages (arm64 + amd64).
FROM postgres:16-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-16-postgis-3 \
 && rm -rf /var/lib/apt/lists/* \
 # gosu is only used by the entrypoint to drop root; this image never runs as root, and the
 # upstream binary carries known Go stdlib CVEs (trivy, Phase 1).
 && rm -f /usr/local/bin/gosu

COPY --chown=postgres:postgres init/ /docker-entrypoint-initdb.d/

# Run as the unprivileged postgres user from the start (no root entrypoint). The official image
# supports this; the data directory is owned by postgres in the image (trivy DS-0002).
USER postgres
