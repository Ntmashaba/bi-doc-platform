"""Check that components/ matches the standalone repositories it was imported from.

    python scripts/verify_components.py <pbi-doc-gen checkout> <adf-doc-gen checkout>

Each checkout must be at the commit recorded in components/README.md, and this repository at the import commit
(63d210c): components/ has changed since, on purpose, so it no longer matches the sources. Files present in both trees must be
identical, apart from the deliberate differences listed below; anything else is reported and fails the run.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKOUTS = (("power-bi", "0b04cf41d1cff0e4d7fc3587fff6e14f86fd405f"),
             ("adf", "960b4bc165efff415d1adb4932fb39196fc9eff1"))
# Deliberate differences (see components/README.md).
CHANGED = {"generate_docs.py", "pyproject.toml", "tests/test_packaging.py"}
ADDED = {"pbidocgen/cli.py", "adfdocgen/cli.py"}
NOT_IMPORTED = ("pbi-tools/", "pbix-samples/")
KEPT_SAMPLES = ("pbix-samples/DP500 ",)


def tracked(repo: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, check=True).stdout
    return [p for p in out.decode().split("\0") if p]


def main(paths: list[str]) -> int:
    if len(paths) != 2:
        print(__doc__)
        return 2
    problems = []
    for (component, commit), repo in zip(CHECKOUTS, map(Path, paths)):
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
        if head != commit:
            problems.append(f"{repo} is at {head}, expected {commit}")
            continue
        mine = ROOT / "components" / component
        for rel in tracked(repo):
            if rel.startswith(NOT_IMPORTED) and not rel.startswith(KEPT_SAMPLES):
                if (mine / rel).exists():
                    problems.append(f"{component}: {rel} should not be imported")
                continue
            if not (mine / rel).is_file():
                problems.append(f"{component}: missing {rel}")
            elif rel not in CHANGED and (mine / rel).read_bytes() != (repo / rel).read_bytes():
                problems.append(f"{component}: differs {rel}")
        known = set(tracked(repo))
        for path in sorted(p for p in mine.rglob("*") if p.is_file() and "__pycache__" not in p.parts
                           and ".egg-info" not in str(p) and "build" not in p.relative_to(mine).parts[:1]):
            rel = path.relative_to(mine).as_posix()
            if rel not in known and rel not in ADDED:
                problems.append(f"{component}: unexpected {rel}")
    for line in problems:
        print(line)
    print("components match their sources" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
