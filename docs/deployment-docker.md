# Running the library in Docker

The image holds the library and the local backend (SQLite catalogue plus immutable artifact
files) under `/data`. It runs as UID 10001, and its health check calls `/api/v1/health/ready`.

```sh
docker build -t bidoc-library .
docker volume create bidoc-data
docker run -d --name bidoc --restart unless-stopped \
  -v bidoc-data:/data -p 127.0.0.1:8765:8765 \
  -e LOCAL_CONTAINER_BIND=published-on-host-loopback-only \
  bidoc-library
```

Then open <http://127.0.0.1:8765/>.

## Why the acknowledgement

In local mode, whoever reaches the port acts as the library's single owner. Outside a
container the library only listens on loopback. Inside one it must listen on the container
interface, so it refuses to start until you set
`LOCAL_CONTAINER_BIND=published-on-host-loopback-only`. Setting it states that the port is
published on the host's loopback only (`-p 127.0.0.1:…`).

The Host and Origin checks, the session secret and the anti-CSRF header still apply. The
session secret is created in `/data/session-secret` on first start and survives
re-creation.

If you publish on a different host port, add it to the allowed hosts, for example
`-p 127.0.0.1:9000:8765 -e ALLOWED_HOSTS=127.0.0.1:9000`.

## Sharing with other people

Do not publish local mode on a network address. Use `AUTH_MODE=gateway` behind an
authenticating reverse proxy, with `GATEWAY_TRUSTED_PROXIES` set to the proxy's address
(see `docs/library-api.md`).

## Data and upgrades

- Everything lives in the `/data` volume. Removing the container keeps it.
- Migrations run on start. Back up before upgrading with `LocalStore.backup(path)`
  (see `docs/library-storage.md`).

## Verified by

`apps/library/tests/check_docker.py`, which runs in the CI `docker` job. It checks:

- the image refuses to start without the acknowledgement;
- it runs as a non-root user;
- a foreign Host is refused;
- a published document, the session secret and search survive both `docker restart` and
  removing and re-creating the container on the same volume (A03);
- the health check reports healthy.
