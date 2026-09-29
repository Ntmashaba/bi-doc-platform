"""Frozen entry point for bidoc.exe (console)."""
import sys

if len(sys.argv) > 1 and sys.argv[1] == "--portable-extract":
    # The extraction child process: a frozen executable cannot run "python -m", so the parent re-invokes itself.
    sys.argv.pop(1)
    from pbidocgen.portable import main as portable_main
    raise SystemExit(portable_main())

from bidoc_generator.cli import main

if __name__ == "__main__":
    sys.exit(main())
