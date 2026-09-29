"""Render the consolidated analysis payload as a Word document (.docx).

Pure standard library — a .docx is a ZIP of XML parts, so this module writes
the OOXML directly (document, styles, content types, relationships, core
properties). No python-docx, no pip install, honoring the project's
zero-dependency promise.

The Word document is deliberately NOT the HTML flattened. Word is linear and
carries no search or cross-links, so this renderer produces the narrative
subset a stakeholder or handover pack needs: overview, sources, usage
verdicts with evidence, per-page feeds, filters, warnings — then a reference
appendix (tables, measures, relationships, security). The HTML remains the
working document.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape
from .page_references import page_label

# --------------------------------------------------------------------------
# Page geometry (A4, 2 cm margins) — all values in DXA (1440 = 1 inch)
# --------------------------------------------------------------------------
PAGE_W, PAGE_H, MARGIN = 11906, 16838, 1134
CONTENT_W = PAGE_W - 2 * MARGIN  # usable width for tables

ACCENT = "B45F2A"       # copper — model side, matches the HTML identity
ACCENT2 = "6B5B95"      # violet — report side
GREY_HDR = "EFECE6"     # table header shading
GREY_CODE = "F5F3EE"    # code block shading
MUTED = "6B6B6B"

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _t(text) -> str:
    """Escape text for XML and strip control chars Word rejects."""
    return escape(_CTRL.sub("", str(text if text is not None else "")))


# --------------------------------------------------------------------------
# Low-level OOXML builders
# --------------------------------------------------------------------------

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


def para(runs_xml: str, style: str | None = None, shade: str | None = None,
         space_after: int | None = None, keep_next: bool = False) -> str:
    props = []
    if style:
        props.append(f'<w:pStyle w:val="{style}"/>')
    if keep_next:
        props.append("<w:keepNext/>")
    if shade:
        props.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>')
    if space_after is not None:
        props.append(f'<w:spacing w:after="{space_after}"/>')
    ppr = f"<w:pPr>{''.join(props)}</w:pPr>" if props else ""
    return f"<w:p>{ppr}{runs_xml}</w:p>"


def text_para(text, style=None, **kw) -> str:
    return para(run(text), style=style, **kw)


def heading(text, level: int) -> str:
    return text_para(text, style=f"Heading{level}")


def code_block(text: str) -> str:
    """Each line is its own shaded paragraph — never \\n inside a run."""
    out = []
    lines = (text or "").splitlines() or [""]
    for line in lines:
        out.append(para(run(line, font="Consolas", size=8),
                        style="CodeLine", shade=GREY_CODE))
    return "".join(out)


def _cell(content_xml: str, width: int, shade: str | None = None,
          bold_hdr: bool = False) -> str:
    props = [f'<w:tcW w:w="{width}" w:type="dxa"/>']
    if shade:
        props.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>')
    return f"<w:tc><w:tcPr>{''.join(props)}</w:tcPr>{content_xml}</w:tc>"


def table(headers: list[str], rows: list[list], proportions: list[float] | None = None,
          col_styles: list[dict] | None = None) -> str:
    """Simple bordered table. Cell values may be strings or pre-built run XML
    (wrap with ('xml', runs) tuple). Widths set on table AND every cell (DXA)."""
    n = len(headers)
    proportions = proportions or [1.0 / n] * n
    widths = [int(CONTENT_W * p) for p in proportions]
    widths[-1] = CONTENT_W - sum(widths[:-1])  # exact sum

    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    border = '<w:tblBorders>' + "".join(
        f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="D8D3C8"/>'
        for side in ("top", "left", "bottom", "right", "insideH", "insideV")
    ) + '</w:tblBorders>'

    def row_xml(cells, is_header=False):
        tcs = []
        for i, val in enumerate(cells[:n]):
            if isinstance(val, tuple) and val and val[0] == "xml":
                content = para(val[1], style="TableText")
            else:
                content = para(run(val, bold=is_header,
                                   size=8 if not is_header else 8,
                                   color="3A362F" if is_header else None),
                               style="TableText")
            tcs.append(_cell(content, widths[i], GREY_HDR if is_header else None))
        trpr = "<w:trPr><w:tblHeader/></w:trPr>" if is_header else ""
        return f"<w:tr>{trpr}{''.join(tcs)}</w:tr>"

    body = row_xml(headers, is_header=True)
    for r in rows:
        body += row_xml([("" if c is None else c) for c in r])

    return (f'<w:tbl><w:tblPr><w:tblW w:w="{CONTENT_W}" w:type="dxa"/>{border}'
            f'<w:tblLayout w:type="fixed"/></w:tblPr>'
            f'<w:tblGrid>{grid}</w:tblGrid>{body}</w:tbl>'
            + text_para("", space_after=60))  # spacer so tables don't fuse


def page_break() -> str:
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


# --------------------------------------------------------------------------
# Static package parts
# --------------------------------------------------------------------------

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_APP = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">
<Application>pbi-doc-gen</Application></Properties>"""


def _core(title: str, generated: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>{_t(title)}</dc:title>
<dc:creator>pbi-doc-gen</dc:creator>
<dc:description>Power BI documentation generated {_t(generated)}</dc:description>
</cp:coreProperties>"""


def _style(sid, name, *, based="Normal", size=None, bold=False, color=None,
           font=None, outline=None, before=0, after=80, keep_next=False) -> str:
    rpr = []
    if font:
        rpr.append(f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:cs="{font}"/>')
    if bold:
        rpr.append("<w:b/>")
    if color:
        rpr.append(f'<w:color w:val="{color}"/>')
    if size:
        rpr.append(f'<w:sz w:val="{size * 2}"/><w:szCs w:val="{size * 2}"/>')
    # OOXML enforces pPr child order: keepNext → spacing → outlineLvl
    ppr = []
    if keep_next:
        ppr.append("<w:keepNext/>")
    ppr.append(f'<w:spacing w:before="{before}" w:after="{after}"/>')
    if outline is not None:
        ppr.append(f'<w:outlineLvl w:val="{outline}"/>')
    return (f'<w:style w:type="paragraph" w:styleId="{sid}">'
            f'<w:name w:val="{name}"/><w:basedOn w:val="{based}"/>'
            f'<w:pPr>{"".join(ppr)}</w:pPr>'
            f'<w:rPr>{"".join(rpr)}</w:rPr></w:style>')


_STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:docDefaults><w:rPrDefault><w:rPr>'
           '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>'
           '<w:sz w:val="20"/><w:szCs w:val="20"/><w:color w:val="26231E"/>'
           '</w:rPr></w:rPrDefault>'
           '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="264" w:lineRule="auto"/>'
           '</w:pPr></w:pPrDefault></w:docDefaults>'
           '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
           '<w:name w:val="Normal"/></w:style>'
           + _style("Title", "Title", size=26, bold=True, color="26231E",
                    font="Calibri Light", after=40)
           + _style("Subtitle", "Subtitle", size=10, color=MUTED, after=240)
           + _style("Heading1", "heading 1", size=16, bold=True, color=ACCENT,
                    font="Calibri Light", outline=0, before=320, after=120, keep_next=True)
           + _style("Heading2", "heading 2", size=12, bold=True, color="3A362F",
                    outline=1, before=240, after=80, keep_next=True)
           + _style("Heading3", "heading 3", size=10, bold=True, color=ACCENT2,
                    outline=2, before=200, after=60, keep_next=True)
           + _style("CodeLine", "Code Line", size=8, font="Consolas", after=0)
           + _style("TableText", "Table Text", size=8, after=20)
           + _style("Muted", "Muted", size=8, color=MUTED, after=60)
           + '</w:styles>')


# --------------------------------------------------------------------------
# Content assembly
# --------------------------------------------------------------------------

_MODE_NOTES = {
    "combined": (
        "Both the semantic model and the report were supplied, so this document can "
        "trace end-to-end lineage and state, per table, whether and how the report "
        "uses it. \u201cNo references\u201d still only means this report — other reports, "
        "Excel connections, or composite models sharing the model are not visible here."
    ),
    "semantic-only": (
        "Only the semantic model was supplied. Nothing in this document can be called "
        "\u201cunused\u201d: the strongest statement made is \u201cno internal references\u201d, "
        "meaning nothing inside the model itself uses the item. Reports, Excel "
        "connections, or composite models may still depend on it."
    ),
    "report-only": (
        "Only the report was supplied. Field references are documented as unresolved "
        "requirements — the document cannot confirm they exist in any model, reliably "
        "distinguish measures from columns, or trace anything to a data source. "
        "Treat the field manifest as a rebuild specification."
    ),
}


def _join(items, sep=", ", empty="—"):
    items = [str(i) for i in items if i]
    return sep.join(items) if items else empty


def build_docx_body(payload: dict) -> str:
    mode = payload["mode"]
    model = payload.get("model")
    report = payload.get("report")
    linked = payload.get("linked")
    parts: list[str] = []

    # ---- cover -----------------------------------------------------------
    parts.append(text_para(payload["title"], style="Title"))
    inputs = []
    if model:
        inputs.append(f"semantic model \u201c{model['name']}\u201d")
    if report:
        inputs.append(f"report \u201c{report['name']}\u201d")
    parts.append(text_para(
        f"Power BI documentation · mode: {mode} · generated {payload['generated']} · "
        f"inputs: {_join(inputs)}", style="Subtitle"))
    parts.append(para(run("What this document can claim.  ", bold=True)
                      + run(_MODE_NOTES[mode]), shade=GREY_HDR, space_after=240))

    # ---- overview --------------------------------------------------------
    parts.append(heading("Overview", 1))
    stats = []
    if model:
        n_cols = sum(len(t["columns"]) for t in model["tables"])
        stats += [["Tables", len(model["tables"])],
                  ["Columns", n_cols],
                  ["Measures", len(model["measures"])],
                  ["Relationships", len(model["relationships"])],
                  ["RLS roles", len(model["roles"])]]
    if report:
        n_vis = sum(len(p["visuals"]) for p in report["pages"])
        n_filters = (len(report["reportFilters"])
                     + sum(len(p["filters"]) for p in report["pages"]))
        stats += [["Report pages", len(report["pages"])],
                  ["Visuals", n_vis],
                  ["Filters (all scopes)", n_filters],
                  ["Distinct fields referenced", len(report["manifest"])]]
    n_warn = (len((model or {}).get("warnings", []))
              + len((report or {}).get("warnings", []))
              + len((linked or {}).get("warnings", [])))
    stats.append(["Warnings flagged", n_warn])
    parts.append(table(["Metric", "Value"], stats, [0.6, 0.4]))

    if linked:
        verdicts: dict[str, int] = {}
        for u in linked["tableUsage"]:
            verdicts[u["usage"]] = verdicts.get(u["usage"], 0) + 1
        parts.append(heading("Usage at a glance", 2))
        order = ["direct", "via measures", "possible", "none"]
        parts.append(table(
            ["Verdict", "Tables", "Meaning"],
            [[v, verdicts.get(v, 0), {
                "direct": "Bound in a visual, filter, or bookmark.",
                "via measures": "Reached only through the DAX of measures the report uses.",
                "possible": "Connected by an active relationship to a used table.",
                "none": "Nothing in this report reaches it, directly or indirectly.",
            }[v]] for v in order],
            [0.2, 0.12, 0.68]))

    # ---- data sources ----------------------------------------------------
    if model:
        parts.append(heading("Data sources", 1))
        parts.append(text_para(
            "Extracted statically from partition M expressions. Tables whose partitions "
            "contain a native SQL query are marked — their transformations happen "
            "upstream of Power Query.", style="Muted"))
        rows = []
        for t in model["tables"]:
            for p in t["partitions"]:
                s = p["source"]
                loc = _join([s.get("server"), s.get("database")], " / ")
                obj = s.get("object") or s.get("detail") or s.get("path") or s.get("url") or "—"
                native = " (native query)" if s.get("nativeQuery") else ""
                rows.append([s["sourceType"] + native, loc, obj, t["name"]])
        rows.sort(key=lambda r: (r[0], r[1], r[3]))
        parts.append(table(["Source type", "Server / database", "Object", "Feeds table"],
                           rows, [0.22, 0.26, 0.30, 0.22]))

    # ---- inventories use the same report/page rows as HTML and CSV ------
    if payload.get("tableSources"):
        parts.append(heading("Table sources by report page", 1))
        parts.append(text_para("One row per report page, model table and partition. Blank pages identify unresolved or absent page usage.", style="Muted"))
        parts.append(table(["Report / page [ID]", "Model table / partition", "Page usage; server / database", "Source object / query"],
            [[page_label(r), r["table"] + " / " + r["partition"],
              r["usage"] + "; " + _join([r["server"], r["database"]], " / "),
              _join([r["object"], r["query"]], "\n")]
             for r in payload["tableSources"]], [0.25, 0.20, 0.25, 0.30]))
    if payload.get("columns"):
        parts.append(heading("Column usage by report page", 1))
        parts.append(text_para(payload["columns"]["scope"], style="Muted"))
        parts.append(table(["Report / page [ID]", "Column", "Page usage", "Deletion assessment / measures"],
            [[page_label(r), f"{r['table']}[{r['column']}]", r["pageUsage"],
              r["decision"] + "; " + _join(r["pageMeasures"])]
             for r in payload["columns"]["rows"]], [0.28, 0.24, 0.20, 0.28]))

    # ---- pages: what feeds them (report modes) ---------------------------
    if report:
        parts.append(page_break())
        parts.append(heading("Report pages — what feeds each page", 1))
        for pg in report["pages"]:
            flags = []
            if pg["hidden"]:
                flags.append("hidden")
            if pg.get("isActive"):
                flags.append("landing page")
            suffix = f"  ({', '.join(flags)})" if flags else ""
            parts.append(heading(pg["label"] + suffix, 2))

            # rolled-up feed: table -> fields used on this page
            feed = pg.get("feeds", [])
            if feed:
                parts.append(table(
                    ["Model table", "Fields used on this page", "Page usage"],
                    [[r["table"], _join(r["fields"]), r["usage"]] for r in feed],
                    [0.25, 0.50, 0.25]))
            else:
                parts.append(text_para("No field bindings found on this page.", style="Muted"))

            if pg["visuals"]:
                vrows = []
                for vis in pg["visuals"]:
                    bindings = _join(
                        f"{f['table']}[{f['field']}]" if f["table"] else f"[{f['field']}]"
                        for f in vis["fields"])
                    vrows.append([vis["title"] or "—", vis["type"], bindings])
                parts.append(table(["Visual", "Type", "Bindings"], vrows, [0.22, 0.16, 0.62]))

            pg_filters = [f for f in report.get("filterRows", []) if f["pageId"] == pg["id"]]
            if pg_filters:
                parts.append(table(
                    ["Filter scope", "Field", "Type", "Condition hint"],
                    [[f["level"], (f"{f['table']}[{f['field']}]" if f["table"] else f["field"] or "—"),
                      f["filterType"], f["raw"] or "—"] for f in pg_filters],
                    [0.14, 0.34, 0.16, 0.36]))

        if report["reportFilters"]:
            parts.append(heading("Report-level filters (apply everywhere)", 2))
            parts.append(table(
                ["Field", "Type", "Condition hint"],
                [[(f"{f['table']}[{f['field']}]" if f["table"] else f["field"] or "—"),
                  f["filterType"], f["raw"] or "—"] for f in report["reportFilters"]],
                [0.4, 0.2, 0.4]))

        if report["bookmarks"]:
            parts.append(heading("Bookmarks", 2))
            parts.append(table(
                ["Bookmark", "Fields referenced"],
                [[b["name"], _join(f"{f['table']}[{f['field']}]" if f["table"] else f"[{f['field']}]"
                                   for f in b["fields"])] for b in report["bookmarks"]],
                [0.3, 0.7]))

    # ---- field manifest --------------------------------------------------
    if report:
        parts.append(heading("Field manifest — every field the report references", 1))
        if mode == "report-only":
            parts.append(text_para(
                "No model was supplied, so each entry below is an unresolved requirement. "
                "This list is the specification a replacement model must satisfy.",
                style="Muted"))
        manifest = (linked or {}).get("manifest") or report["manifest"]
        rows = []
        for e in manifest:
            ref = f"{e['table']}[{e['field']}]" if e.get("table") else f"[{e['field']}]"
            resolution = e.get("resolution", e.get("kind", "?"))
            rows.append([ref, resolution, _join(e["usedIn"], "; ")])
        res_hdr = "Resolved as" if linked else "Appears as"
        parts.append(table(["Reference", res_hdr, "Used in"], rows, [0.28, 0.14, 0.58]))

    # ---- warnings --------------------------------------------------------
    all_warnings = ([*(model or {}).get("warnings", []),
                     *(report or {}).get("warnings", []),
                     *(linked or {}).get("warnings", [])])
    parts.append(heading("Warnings", 1))
    if all_warnings:
        parts.append(text_para("Read this section before changing anything.", style="Muted"))
        parts.append(table(
            ["Severity", "Category", "Detail"],
            [[w["severity"], w["category"], w["message"]] for w in all_warnings],
            [0.11, 0.2, 0.69]))
    else:
        parts.append(text_para("Nothing flagged by the static analysis.", style="Muted"))

    # ---- appendix: model reference ---------------------------------------
    if model:
        parts.append(page_break())
        parts.append(heading("Appendix A — Tables", 1))
        for t in model["tables"]:
            badge = t["tableType"] + (", hidden" if t["isHidden"] else "")
            parts.append(heading(f"{t['name']}  ({badge})", 3))
            if t.get("description"):
                parts.append(text_para(t["description"], style="Muted"))
            col_rows = []
            for c in t["columns"]:
                notes = []
                if c["isCalculated"]:
                    notes.append("calculated")
                if c["isHidden"]:
                    notes.append("hidden")
                if c.get("reportUsage"):
                    notes.append(f"report usage: {c['reportUsage']}")
                elif c.get("internallyReferenced"):
                    notes.append("internally referenced")
                if c.get("dependsOn"):
                    notes.append("depends on " + _join(c["dependsOn"]))
                col_rows.append([c["name"], c["dataType"], _join(notes, "; ", "")])
            if col_rows:
                parts.append(table(["Column", "Type", "Notes"], col_rows, [0.3, 0.16, 0.54]))
            level_sep = " \u203a "
            for h in t["hierarchies"]:
                parts.append(text_para(
                    f"Hierarchy \u201c{h['name']}\u201d: {_join(h['levels'], level_sep)}",
                    style="Muted"))
            for p_ in t["partitions"]:
                s = p_["source"]
                src_line = s["sourceType"]
                detail = _join([s.get("server"), s.get("database"),
                                s.get("object") or s.get("detail")], " / ", "")
                if detail:
                    src_line += f" — {detail}"
                parts.append(text_para(
                    f"Partition \u201c{p_['name']}\u201d ({p_['mode']}): {src_line}",
                    style="Muted", keep_next=True))
                if p_["expression"]:
                    parts.append(code_block(p_["expression"]))

        parts.append(heading("Appendix B — Measures", 1))
        for m in model["measures"]:
            used = ""
            if linked:
                used = "  — used in report" if m.get("usedInReport") else "  — not used in report"
            parts.append(heading(f"{m['name']}  ({m['table']}){used}", 3))
            if m.get("description"):
                parts.append(text_para(m["description"], style="Muted"))
            parts.append(code_block(m["expression"]))
            deps = []
            if m["dependsOnMeasures"]:
                deps.append("calls measures: " + _join(m["dependsOnMeasures"]))
            if m["dependsOnColumns"]:
                deps.append("reads columns: " + _join(m["dependsOnColumns"]))
            if m["allTables"]:
                deps.append("ultimately touches tables: " + _join(m["allTables"]))
            if m.get("usedBy"):
                deps.append("used by: " + _join(m["usedBy"], "; "))
            if deps:
                parts.append(text_para(". ".join(deps) + ".", style="Muted"))

        parts.append(heading("Appendix C — Relationships", 1))
        rel_rows = []
        for r in model["relationships"]:
            flags = []
            if not r["isActive"]:
                flags.append("inactive")
            if r["crossFilteringBehavior"] == "bothDirections":
                flags.append("bidirectional")
            rel_rows.append([
                f"{r['fromTable']}[{r['fromColumn']}]",
                f"{r['toTable']}[{r['toColumn']}]",
                f"{r['fromCardinality']}:{r['toCardinality']}",
                _join(flags, ", ", "active")])
        parts.append(table(["From (many side)", "To (one side)", "Cardinality", "Status"],
                           rel_rows, [0.32, 0.32, 0.14, 0.22]))

        parts.append(heading("Appendix D — Row-level security", 1))
        if model["roles"]:
            for role in model["roles"]:
                parts.append(heading(f"{role['name']}  ({role['modelPermission']})", 3))
                if role["tablePermissions"]:
                    for tp in role["tablePermissions"]:
                        parts.append(text_para(f"Filter on {tp['table']}:",
                                               style="Muted", keep_next=True))
                        parts.append(code_block(tp["filterExpression"]))
                else:
                    parts.append(text_para("No table filters (role scopes access only).",
                                           style="Muted"))
        else:
            parts.append(text_para(
                "No RLS roles are defined — every user with access sees all data. "
                "This is itself important information.", style="Muted"))

    sect = (f'<w:sectPr><w:pgSz w:w="{PAGE_W}" w:h="{PAGE_H}"/>'
            f'<w:pgMar w:top="{MARGIN}" w:right="{MARGIN}" w:bottom="{MARGIN}" '
            f'w:left="{MARGIN}" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>')

    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body>{"".join(parts)}{sect}</w:body></w:document>')


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def render_docx(payload: dict, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    document = build_docx_body(payload)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        z.writestr("word/styles.xml", _STYLES)
        z.writestr("docProps/core.xml", _core(payload["title"], payload["generated"]))
        z.writestr("docProps/app.xml", _APP)
    return out_path
