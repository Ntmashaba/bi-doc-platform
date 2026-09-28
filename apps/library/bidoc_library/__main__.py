"""Run the library: python -m bidoc_library (configuration from the environment)."""
import sys

from .config import ConfigError, from_env


def main() -> int:
    try:
        settings = from_env()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
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
