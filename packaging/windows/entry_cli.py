"""Frozen entry point for bidoc.exe (console)."""
import sys

from bidoc_generator.cli import main

if __name__ == "__main__":
    sys.exit(main())
