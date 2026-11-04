"""Check the links between the repository's Markdown files, offline.

    python scripts/check_links.py            # every tracked .md file
    python scripts/check_links.py README.md  # only these

For each Markdown link or image whose target is a path in this repository (not http(s), mailto or a bare #anchor of
another site), the target must exist, and an #anchor must match a heading in the target Markdown file (or an explicit
<a id> / <a name>), using GitHub's heading slugs. Links inside code blocks and inline code are ignored. External links
are never fetched, so a site being down never fails this check. Exit code 1 lists every broken link.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
_FENCE = re.compile(r"^\s*(```|~~~)")
_INLINE_CODE = re.compile(r"`+[^`]*`+")
_LINK = re.compile(r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_HTML_ANCHOR = re.compile(r"<a\s+[^>]*(?:id|name)=\"([^\"]+)\"", re.I)
_EXTERNAL = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)


def slug(text: str) -> str:
    """GitHub's anchor for a heading: lower case, markup and punctuation dropped, spaces to hyphens."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)          # a link in a heading keeps its text
    text = re.sub(r"<[^>]+>", "", text).replace("`", "").replace("*", "").strip().lower()
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def _lines(text: str, keep_code: bool = False):
    """(line number, line) outside fenced code blocks; inline code removed unless `keep_code` (a heading keeps it)."""
    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            yield number, line if keep_code else _INLINE_CODE.sub("", line)


def anchors(path: Path, cache: dict) -> set:
    if path not in cache:
        found, seen = set(), {}
        text = path.read_text(encoding="utf-8")
        for _, line in _lines(text, keep_code=True):
            heading = _HEADING.match(line)
            if heading:
                base = slug(heading.group(2))
                count = seen.get(base, 0)
                found.add(base if count == 0 else f"{base}-{count}")
                seen[base] = count + 1
            found.update(_HTML_ANCHOR.findall(line))
        cache[path] = found
    return cache[path]


def check(files) -> list[str]:
    problems, cache = [], {}
    for md in files:
        text = md.read_text(encoding="utf-8")
        for number, line in _lines(text):
            for target in _LINK.findall(line):
                if _EXTERNAL.match(target) or target.startswith("//"):
                    continue
                where = f"{md.relative_to(ROOT).as_posix()}:{number}"
                path_part, _, anchor = target.partition("#")
                path_part = unquote(path_part)
                resolved = (md.parent / path_part).resolve() if path_part else md
                try:
                    resolved.relative_to(ROOT)
                except ValueError:
                    problems.append(f"{where}: {target} points outside the repository")
                    continue
                if not resolved.exists():
                    problems.append(f"{where}: {target} does not exist")
                    continue
                if anchor and resolved.suffix.lower() == ".md" and unquote(anchor) not in anchors(resolved, cache):
                    problems.append(f"{where}: {target} has no heading with the anchor #{anchor}")
    return problems


def tracked_markdown() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / line for line in out.splitlines() if line]


def main(argv=None) -> int:
    args = (argv if argv is not None else sys.argv[1:])
    files = [Path(a).resolve() for a in args] if args else tracked_markdown()
    problems = check(files)
    for problem in problems:
        print(problem)
    print(f"{len(files)} Markdown file(s) checked; {len(problems)} broken link(s).")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
