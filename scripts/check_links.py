"""Check the links between the repository's Markdown files, offline.

    python scripts/check_links.py            # every tracked .md file
    python scripts/check_links.py README.md  # only these

Every link whose target is a path in this repository must point at a file or folder that exists, and an #anchor must
match a heading of the target Markdown file (GitHub's slugs, duplicates numbered) or an explicit <a id> / <a name>.
External links (any scheme, such as https: or mailto:) are never fetched, so a site being down never fails this check.
Exit code 1 lists every broken link with its file and line.

Read, following CommonMark where it matters here:

* inline links and images: [text](dest), ![alt](dest "title"), dest in <angle brackets> (spaces allowed), balanced
  parentheses, backslash escapes, link text across lines;
* reference links: definitions ([label]: dest "title") are checked like inline links. A reference to an undefined
  label is plain text on GitHub, not a link, so it is reported only where it is most likely a typo: a full or
  collapsed reference ([text][label], [label][]) in a file that defines reference links of its own. Citation markers
  such as [1][2] in a file with no definitions are prose. A shortcut ([label]) is a link only when the label is defined;
* destinations are unescaped and %-decoded before they are looked up;
* fenced code blocks (``` or ~~~, three or more, up to three spaces of indent; closed only by the same character at
  least as long, and running to the end of the file if never closed) and inline code spans (a run of backticks closed
  by a run of the same length) are ignored;
* ATX headings (# ...) and setext headings (a line underlined with === or ---) give anchors.

Not read: links in raw HTML (<a href>), autolinks (<https://...>, always external), and indented (four-space) code
blocks, which this repository does not use; a link written in one would be checked.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]

_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_HTML_ANCHOR = re.compile(r"<a\s+[^>]*(?:id|name)\s*=\s*[\"']([^\"']+)[\"']", re.I)
_EXTERNAL = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)
_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")
# Link text: anything but brackets, or one level of nested brackets; may cross lines.
_TEXT = r"(?:[^\[\]\\]|\\.|\[(?:[^\[\]\\]|\\.)*\])*"
# A destination: <...> (no newline or >), or a run without spaces whose parentheses balance (one level deep).
_PUNCT_ESCAPE = r"\\[!-/:-@\[-`{-~]"                   # only ASCII punctuation can be escaped
_DEST = (r"(<(?:[^<>\n\\]|" + _PUNCT_ESCAPE + r")*>|(?:[^\s()<>\\]|" + _PUNCT_ESCAPE + r"|\((?:[^\s()\\]|"
         + _PUNCT_ESCAPE + r")*\))+)")
_TITLE = r"(?:[ \t\n]+(?:\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|\((?:[^()\\]|\\.)*\)))?"
_INLINE = re.compile(r"!?\[" + _TEXT + r"\]\([ \t\n]*" + _DEST + _TITLE + r"[ \t\n]*\)", re.S)
_DEFINITION = re.compile(r"^ {0,3}\[((?:[^\[\]\\]|\\.)+)\]:[ \t]*\n?[ \t]*" + _DEST + _TITLE + r"[ \t]*$", re.M)
_REFERENCE = re.compile(r"!?\[(" + _TEXT + r")\]\[((?:[^\[\]\\]|\\.)*)\]", re.S)
_SHORTCUT = re.compile(r"(?<![\]\\])!?\[((?:[^\[\]\\]|\\.)+)\](?![\[(:])")


def slug(text: str) -> str:
    """GitHub's anchor for a heading: lower case, markup and punctuation dropped, spaces to hyphens."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)          # a link in a heading keeps its text
    text = re.sub(r"<[^>]+>", "", text).replace("`", "").replace("*", "").strip().lower()
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def _label(text: str) -> str:
    return " ".join(text.split()).casefold()


def _blank(text: str) -> str:
    """The same text, line for line, with every character that is not a newline replaced by a space."""
    return re.sub(r"[^\n]", " ", text)


def without_fences(text: str) -> tuple[str, list[str]]:
    """(text with fenced code blanked out, lines outside fences with their code kept, for headings)."""
    out, fence = [], None
    for line in text.split("\n"):
        if fence is None:
            opened = _FENCE_OPEN.match(line)
            if opened and not (opened.group(1)[0] == "`" and "`" in opened.group(2)):   # ``` info may not hold `
                fence = opened.group(1)
                out.append(_blank(line))
                continue
            out.append(line)
        else:
            closing = re.match(r"^ {0,3}(" + re.escape(fence[0]) + r"{" + str(len(fence)) + r",})[ \t]*$", line)
            if closing:
                fence = None
            out.append(_blank(line))
    return "\n".join(out), out


def without_code_spans(text: str) -> str:
    """Inline code blanked out: a run of N backticks is closed by the next run of exactly N backticks."""
    out, i = [], 0
    for run in re.finditer(r"`+", text):
        if run.start() < i:
            continue
        n = len(run.group())
        closer = re.compile(r"(?<!`)`{" + str(n) + r"}(?!`)")
        end = closer.search(text, run.end())
        if end is None:
            continue                                           # an unmatched run is literal backticks
        out.append(text[i:run.start()])
        out.append(_blank(text[run.start():end.end()]))
        i = end.end()
    out.append(text[i:])
    return "".join(out)


def anchors(path: Path, cache: dict) -> set:
    if path not in cache:
        found, seen = set(), {}
        _, lines = without_fences(path.read_text(encoding="utf-8"))

        def add(heading: str) -> None:
            base = slug(heading)
            count = seen.get(base, 0)
            found.add(base if count == 0 else f"{base}-{count}")
            seen[base] = count + 1
        for number, line in enumerate(lines):
            atx = _HEADING.match(line)
            if atx:
                add(atx.group(2) or "")
            elif (number + 1 < len(lines) and line.strip() and _SETEXT.match(lines[number + 1])
                  and not _SETEXT.match(line) and not line.lstrip().startswith(("-", "*", ">", "|"))):
                add(line.strip())
            found.update(_HTML_ANCHOR.findall(line))
        cache[path] = found
    return cache[path]


def _destination(raw: str) -> str:
    dest = raw[1:-1] if raw.startswith("<") and raw.endswith(">") else raw
    return _ESCAPE.sub(r"\1", dest)


def links(text: str) -> list[tuple[int, str]]:
    """(line, destination) for every link in a Markdown text, code ignored."""
    code_free = without_code_spans(without_fences(text)[0])
    line_of = lambda pos: code_free.count("\n", 0, pos) + 1  # noqa: E731
    found, definitions = [], {}
    for d in _DEFINITION.finditer(code_free):
        definitions.setdefault(_label(d.group(1)), d.group(2))
        found.append((line_of(d.start(2)), d.group(2)))
    used = []
    for m in _INLINE.finditer(code_free):
        found.append((line_of(m.start(1)), m.group(1)))
        used.append((m.start(), m.end()))
    inside = lambda pos: any(a <= pos < b for a, b in used)  # noqa: E731
    for r in _REFERENCE.finditer(code_free):
        if inside(r.start()):
            continue
        label = _label(r.group(2) or r.group(1))
        target = definitions.get(label)
        if target is not None:
            found.append((line_of(r.start()), target))
        elif definitions:                                  # this file uses references: an unknown label is a typo
            found.append((line_of(r.start()), f"\0undefined:{r.group(2) or r.group(1)}"))
        used.append((r.start(), r.end()))
    for s in _SHORTCUT.finditer(code_free):
        if not inside(s.start()) and _label(s.group(1)) in definitions:
            found.append((line_of(s.start()), definitions[_label(s.group(1))]))
    return sorted(found)


def check(files) -> list[str]:
    problems, cache = [], {}
    for md in files:
        for number, raw in links(md.read_text(encoding="utf-8")):
            where = f"{md.relative_to(ROOT).as_posix()}:{number}"
            if raw.startswith("\0undefined:"):
                problems.append(f"{where}: [{raw.split(':', 1)[1]}] is a reference with no definition")
                continue
            target = _destination(raw)
            if _EXTERNAL.match(target) or target.startswith("//"):
                continue
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
    args = argv if argv is not None else sys.argv[1:]
    files = [Path(a).resolve() for a in args] if args else tracked_markdown()
    problems = check(files)
    for problem in problems:
        print(problem)
    print(f"{len(files)} Markdown file(s) checked; {len(problems)} broken link(s).")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
