"""Compatibility entry point; use the installed CLI or python -m pbidocgen.cli."""
from pbidocgen.cli import *  # noqa: F401,F403

if __name__ == "__main__":
    cli()
