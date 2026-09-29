"""Compatibility entry point; use the installed CLI or python -m adfdocgen.cli."""
from adfdocgen.cli import *  # noqa: F401,F403

if __name__ == "__main__":
    cli()
