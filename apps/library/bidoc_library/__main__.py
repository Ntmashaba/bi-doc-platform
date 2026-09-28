"""Run the library: python -m bidoc_library (configuration from the environment).

    python -m bidoc_library                   serve
    python -m bidoc_library backup DEST       back up the configured library into an empty folder
    python -m bidoc_library verify SRC        check a backup's files against their checksums
    python -m bidoc_library restore SRC       restore into the configured, empty library
    python -m bidoc_library check             read every committed revision back (after a restore)
"""
import json
import sys

from .config import ConfigError, from_env


def _limits(settings):
    from bidoc_contracts import Limits  # noqa: PLC0415
    return Limits(html_bytes=settings.max_html_bytes, manifest_bytes=settings.max_manifest_bytes,
                  zip_bytes=settings.max_zip_bytes)


def _maintenance(command, args, settings) -> int:
    from . import backup as b  # noqa: PLC0415
    from .api import open_store  # noqa: PLC0415
    try:
        if command == "verify":
            index = b.verify(args[0])
            out = {k: v for k, v in index.items() if k != "files"} | {"file_count": len(index["files"])}
        elif command == "backup":
            out = b.backup(open_store(settings, _limits(settings)), args[0])
        elif command == "restore":
            out = b.restore(args[0], settings=settings, limits=_limits(settings))
        else:
            out = b.check(open_store(settings, _limits(settings)))
    except b.BackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1))
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    commands = {"backup": 1, "verify": 1, "restore": 1, "check": 0}
    if argv and (argv[0] not in commands or len(argv) != commands[argv[0]] + 1):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        settings = from_env()
        settings.validate()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    if argv:
        return _maintenance(argv[0], argv[1:], settings)
    import uvicorn

    from .api import create_app
    app = create_app(settings)
    if settings.auth_mode == "local":
        print(f"Library on http://{settings.bind_host}:{settings.port} (local mode). Changes need the session "
              f"secret in {settings.local_data_dir / 'session-secret'}.", file=sys.stderr)
    uvicorn.run(app, host=settings.bind_host, port=settings.port, log_level=settings.log_level,
                proxy_headers=False, server_header=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
