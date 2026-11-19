"""Render the analysis payload as a Word document (.docx).

Pure standard library — a .docx is a ZIP of XML parts, so this module writes
the OOXML directly.  No python-docx, no pip install, honoring the project's
zero-dependency promise.  Conventions carried over from the ADF engine's
word_writer (which encode real Word/LibreOffice compatibility lessons): A4;
pPr child order keepNext -> spacing; DXA widths on the grid AND every cell,
summing exactly; headings carry outlineLvl so navigation works; code blocks
are one shaded paragraph per line, never a newline inside a run.

The Word document is the narrative subset a handover pack needs: what the
jobs are, how each flows, what they process, when they run, and what the
analysis flagged.  The HTML remains the working document.
"""

from __future__ import annotations

import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

PAGE_W, PAGE_H, MARGIN = 11906, 16838, 1134
CONTENT_W = PAGE_W - 2 * MARGIN

ACCENT = "4338CA"       # indigo — SSAS hue, shared with the HTML
ACCENT2 = "0F766E"      # teal — read/source hue
GREY_HDR = "ECF1F0"
GREY_CODE = "F2F5F4"
MUTED = "6B6B6B"

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _t(text) -> str:
    """Escape text for XML and strip control chars Word rejects."""
    return escape(_CTRL.sub("", str(text if text is not None else "")))


def run(text, bold=False, italic=False, color=None, size=None, font=None) -> str:
    props = []
    if font:
        props.append(f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:cs="{font}"/>')
    if bold:
        props.append("<w:b/>")
    if italic:
        props.append("<w:i/>")
    if color:
        props.append(f'<w:color w:val="{color}"/>')
    if size:
        props.append(f'<w:sz w:val="{size * 2}"/><w:szCs w:val="{size * 2}"/>')
    rpr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{_t(text)}</w:t></w:r>'


def para(runs_xml: str, style=None, shade=None, space_after=None, keep_next=False) -> str:
    props = []
    if style:
        props.append(f'<w:pStyle w:val="{style}"/>')
    if keep_next:
        props.append("<w:keepNext/>")
    if shade:
        props.append(f'<w:shd w:val="clear" w:color="auto" fill="{shade}"/>')
    if space_after is not None:
        props.append(f'<w:spacing w:after="{space_after}"/>')
    ppr = f"<w:pPr>{''.join(props)}</w:pPr>" if props else ""
    return f"<w:p>{ppr}{runs_xml}</w:p>"


def text_para(text, style=None, **kw) -> str:
    return para(run(text), style=style, **kw)


def heading(text, level: int) -> str:
    return text_para(text, style=f"Heading{level}", keep_next=True)


def code_block(text: str) -> str:
    out = []
    for line in str(text).splitlines() or [""]:
        out.append(para(run(line, font="Consolas", size=8), shade=GREY_CODE, space_after=0))
    return "".join(out)


def _cell(content_xml: str, width: int, shade=None) -> str:
    shd = f'<w:shd w:val="clear" w:color="auto" fill="{shade}"/>' if shade else ""
    return (f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shd}</w:tcPr>'
            f"{content_xml}</w:tc>")


def table(headers, rows, proportions=None) -> str:
    n = len(headers)
    proportions = proportions or [1.0 / n] * n
    widths = [int(CONTENT_W * p) for p in proportions]
    widths[-1] = CONTENT_W - sum(widths[:-1])
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)

    def row_xml(cells, is_header=False):
        tcs = []
        for i, cell in enumerate(cells):
            if isinstance(cell, str):
                content = para(run(cell, bold=is_header, size=8))
            else:
                content = cell
            tcs.append(_cell(content, widths[i], GREY_HDR if is_header else None))
        return "<w:tr>" + "".join(tcs) + "</w:tr>"

    body = row_xml(list(headers), is_header=True)
    for r in rows:
        body += row_xml(list(r))
    return ('<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>'
            '<w:tblBorders>'
            + "".join(f'<w:{b} w:val="single" w:sz="4" w:color="D0D7D6"/>'
                      for b in ("top", "left", "bottom", "right", "insideH", "insideV"))
            + "</w:tblBorders></w:tblPr>"
            f"<w:tblGrid>{grid}</w:tblGrid>{body}</w:tbl>")


def page_break() -> str:
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


# --------------------------------------------------------------------------
# Package parts
# --------------------------------------------------------------------------

def _content_types() -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            "</Types>")


def _rels() -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            "</Relationships>")


def _doc_rels() -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>")


def _core(title: str, generated: str) -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:title>{_t(title)}</dc:title>"
            "<dc:creator>sqlagent-doc-gen</dc:creator>"
            f"<dcterms:created xsi:type=\"dcterms:W3CDTF\">{_t(generated)}</dcterms:created>"
            "</cp:coreProperties>")


def _styles() -> str:
    def _style(sid, name, *, based="Normal", size=None, bold=False, color=None, outline=None):
        ppr = f'<w:pPr><w:outlineLvl w:val="{outline}"/></w:pPr>' if outline is not None else ""
        rpr = []
        if bold:
            rpr.append("<w:b/>")
        if color:
            rpr.append(f'<w:color w:val="{color}"/>')
        if size:
            rpr.append(f'<w:sz w:val="{size * 2}"/><w:szCs w:val="{size * 2}"/>')
        return (f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/>'
                f'<w:basedOn w:val="{based}"/>{ppr}'
                f"<w:rPr>{''.join(rpr)}</w:rPr></w:style>")

    styles = [
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/><w:rPr>'
        '<w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI" w:cs="Segoe UI"/>'
        '<w:sz w:val="20"/><w:szCs w:val="20"/></w:rPr></w:style>',
        _style("Title", "Title", size=26, bold=True, color="0F172A", outline=0),
        _style("Heading1", "heading 1", size=16, bold=True, color="0F172A", outline=0),
        _style("Heading2", "heading 2", size=13, bold=True, color=ACCENT, outline=1),
        _style("Heading3", "heading 3", size=11, bold=True, color="334155", outline=2),
    ]
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            + "".join(styles) + "</w:styles>")


# --------------------------------------------------------------------------
# Content
# --------------------------------------------------------------------------

_ACTION = {1: "quit (success)", 2: "quit (failure)", 3: "go to next step", 4: "go to step"}


def _join(items, sep=", ", empty="—"):
    items = [str(i) for i in items if i]
    return sep.join(items) if items else empty


def _action_text(action, step_id):
    if action is None:
        return "default"
    if action == 4:
        return f"go to step {step_id}"
    return _ACTION.get(action, f"action {action}")


def build_docx_body(payload: dict) -> str:
    L = []
    w = L.append
    jobs = payload.get("jobs", [])
    issues = payload.get("issues", [])
    proc = payload.get("processing", {})
    cov = payload.get("coverage", {})

    w(text_para(payload.get("title", "SQL Agent jobs"), style="Title"))
    w(para(run(f"Generated {payload.get('generated', '')} by sqlagent-doc-gen "
               f"{payload.get('engineVersion', '')}", color=MUTED, size=9)))
    w(text_para(
        "Static analysis of scripted SQL Server Agent jobs — no server was contacted. "
        "Findings are evidence, not instructions: each review issue states what the "
        "scripts say, never what to change. Schedules are reported as coinciding; the "
        "scripts contain start times but no durations, so a coincidence does "
        "not show the jobs running at the same time."))

    w(heading("Overview", 1))
    steps = sum(len(j.get("steps", [])) for j in jobs)
    w(table(["jobs", "steps", "SSAS operations", "referenced objects", "review issues",
             "secret values withheld"],
            [[str(len(jobs)), str(steps), str(len(proc.get("operations", []))),
              str(len(payload.get("entities", []))), str(len(issues)),
              str(payload.get("redactions", 0))]],
            [0.16, 0.12, 0.2, 0.2, 0.16, 0.12]))

    if issues:
        w(heading("Review issues", 1))
        order = {"alert": 0, "warning": 1, "info": 2}
        rows = [[i.get("severity", ""), i.get("code", ""), i.get("message", ""),
                 _join([i.get("job"), i.get("step")], " / ")]
                for i in sorted(issues, key=lambda i: order.get(i.get("severity"), 3))]
        w(table(["severity", "code", "finding", "job / step"], rows,
                [0.1, 0.18, 0.5, 0.22]))

    w(heading("Jobs", 1))
    for job in jobs:
        w(heading(job.get("name") or job.get("identity", ""), 2))
        meta = []
        if job.get("description"):
            meta.append(f"Description: {job['description']}")
        meta.append("State: " + ("disabled" if job.get("enabled") == 0 else "enabled"))
        if job.get("owner"):
            meta.append(f"Owner: {job['owner']}")
        if job.get("category"):
            meta.append(f"Category: {job['category']}")
        if job.get("schedules"):
            meta.append("Schedules: " + _join(s.get("summary") or s.get("name")
                                              for s in job["schedules"]))
        w(para(run("; ".join(meta) + ".", color=MUTED, size=9)))
        g = (payload.get("executionGraphs") or {}).get(job.get("identity"), {})
        for note in _graph_notes(g):
            w(para(run(note, italic=True, color=MUTED, size=9)))
        rows = []
        for s in job.get("steps", []):
            ops = s.get("operations", [])
            what = "; ".join(
                f"{o.get('kind', '')}{('/' + o['verb']) if o.get('verb') else ''} "
                f"{o.get('object') or ''}".strip() for o in ops) or None
            if not what and s.get("sql"):
                sql = s["sql"]
                bits = []
                if sql.get("reads"):
                    bits.append("reads " + _join(sql["reads"]))
                if sql.get("writes"):
                    bits.append("writes " + _join(sql["writes"]))
                if sql.get("procs"):
                    bits.append("calls " + _join(sql["procs"]))
                what = "; ".join(bits) or None
            rows.append([
                str(s.get("id") if s.get("id") is not None else "·"),
                s.get("name", ""),
                (s.get("subsystem") or "TSQL"),
                what or ("(command carried, not analysed)"
                         if s.get("commandKind") == "literal" else
                         f"command {s.get('commandKind', 'missing')}"),
                f"{_action_text(s.get('onSuccessAction'), s.get('onSuccessStepId'))} / "
                f"{_action_text(s.get('onFailAction'), s.get('onFailStepId'))}",
                str(s.get("retryAttempts") or 0),
            ])
        if rows:
            w(table(["#", "step", "subsystem", "what it does", "on success / on failure",
                     "retries"], rows, [0.05, 0.17, 0.13, 0.35, 0.22, 0.08]))

    targets = proc.get("targets", [])
    if targets:
        w(heading("Processing (SSAS)", 1))
        w(text_para("The SSAS processing sequence across jobs, grouped by target "
                    "database — separate from the execution flow inside each job."))
        for tgt in targets:
            w(heading(f"{tgt['server']} / {tgt['database']}", 2))
            w(table(["job", "step", "operation", "object"],
                    [[o.get("job", ""), o.get("step", ""),
                      f"{o.get('kind', '')}{('/' + o['verb']) if o.get('verb') else ''}",
                      o.get("object") or "—"]
                     for o in tgt.get("operations", [])],
                    [0.2, 0.2, 0.22, 0.38]))
        if proc.get("coincidence"):
            w(heading("Coincident start times", 2))
            w(text_para("These jobs process the same database and their schedules "
                        "coincide. The scripts contain start times but no durations, "
                        "so a coincidence does not show the jobs running at the "
                        "same time."))
            w(table(["database", "jobs", "schedule"],
                    [[c.get("database", ""), _join(c.get("jobs", [])),
                      c.get("schedule") or "—"] for c in proc["coincidence"]],
                    [0.25, 0.45, 0.3]))

    sched_rows = []
    for job in jobs:
        for s in job.get("schedules", []):
            sched_rows.append([job.get("name", ""), s.get("name") or "(unnamed)",
                               s.get("frequency", ""), s.get("subday") or "—",
                               "disabled" if s.get("enabled") == 0 else "enabled"])
    if sched_rows:
        w(heading("Schedules", 1))
        w(table(["job", "schedule", "frequency", "when", "state"], sched_rows,
                [0.24, 0.2, 0.24, 0.2, 0.12]))

    w(heading("Coverage", 1))
    w(table(["input files", "skipped files", "unrecognised statements",
             "steps not parsed", "unknown operations"],
            [[str(cov.get("inputFiles", 0)), str(len(cov.get("skippedFiles", []))),
              str(cov.get("unrecognisedStatements", 0)),
              str(cov.get("unparsedSteps", 0)), str(cov.get("unknownOperations", 0))]],
            [0.2, 0.2, 0.2, 0.2, 0.2]))
    if payload.get("warnings"):
        w(heading("Warnings", 2))
        for warning in payload["warnings"]:
            w(text_para("• " + str(warning)))
    return "".join(L)


def _graph_notes(graph: dict):
    notes = []
    if graph.get("unreachable"):
        names = ", ".join(u.get("name") or str(u.get("id")) for u in graph["unreachable"])
        notes.append(f"Not reachable from the scripted start step: {names}.")
    if graph.get("loops"):
        notes.append("The scripted step flow loops back; the exit condition lives in "
                     "the step logic, not the flow.")
    return notes


def render_docx(payload: dict, out_path) -> Path:
    out_path = Path(out_path)
    body = build_docx_body(payload)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f"<w:body>{body}"
                f'<w:sectPr><w:pgSz w:w="{PAGE_W}" w:h="{PAGE_H}"/>'
                f'<w:pgMar w:top="{MARGIN}" w:right="{MARGIN}" w:bottom="{MARGIN}" '
                f'w:left="{MARGIN}" w:header="720" w:footer="720" w:gutter="0"/>'
                "</w:sectPr></w:body></w:document>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _content_types())
        z.writestr("_rels/.rels", _rels())
        z.writestr("word/_rels/document.xml.rels", _doc_rels())
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", _styles())
        z.writestr("docProps/core.xml", _core(payload.get("title", ""), stamp))
    return out_path
