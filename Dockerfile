# BI Documentation Platform library (local backend). Build: docker build -t bidoc-library .
#
# Run on one machine, reachable only from that machine:
#   docker run -d --name bidoc -v bidoc-data:/data -p 127.0.0.1:8765:8765 \
#     -e LOCAL_CONTAINER_BIND=published-on-host-loopback-only bidoc-library
# Local mode refuses to start on the container interface without that acknowledgement,
# because anyone who can reach the port would share the owner's session. Publish the port
# on the host loopback only. To share with other people, use AUTH_MODE=gateway behind an
# authenticating proxy (docs/deployment-docker.md).

FROM python:3.11.13-slim-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY requirements-lock.txt ./
COPY packages/contracts packages/contracts
COPY packages/engines packages/engines
COPY packages/relationships packages/relationships
COPY apps/library apps/library
# Engines are fetched at the commits pinned in packages/engines/pyproject.toml; every
# third-party version comes from the lock file.
RUN pip wheel --no-cache-dir --wheel-dir /wheels -c requirements-lock.txt \
        ./packages/contracts ./packages/engines ./packages/relationships ./apps/library

FROM python:3.11.13-slim-bookworm
RUN useradd --uid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin bidoc \
    && mkdir /data && chown bidoc /data
COPY --from=build /wheels /wheels
# The builder resolved every dependency (including the git-pinned engines) into /wheels.
RUN pip install --no-cache-dir --no-index --no-deps /wheels/*.whl && pip check && rm -rf /wheels
ENV LOCAL_DATA_DIR=/data \
    BIND_HOST=0.0.0.0 \
    PORT=8765 \
    PYTHONUNBUFFERED=1
USER bidoc
VOLUME /data
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --start-interval=2s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:' + os.environ.get('PORT', '8765') + '/api/v1/health/ready', headers={'Host': '127.0.0.1:' + os.environ.get('PORT', '8765')}), timeout=4)"]
CMD ["python", "-m", "bidoc_library"]
