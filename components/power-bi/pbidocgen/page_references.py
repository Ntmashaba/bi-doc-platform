"""Canonical page identity and display labels shared by every output format."""
from __future__ import annotations


def page_ref(report, page=None):
    return {"report": report["name"] if report else "Not supplied",
            "pageId": page["id"] if page else "",
            "page": page["name"] if page else ""}


def page_label(ref):
    if not ref.get("pageId"):
        return f"{ref['report']} / No specific page"
    return f"{ref['report']} / {ref['page']} [{ref['pageId']}]"


def attach_report_locations(report):
    """Record structured locations for bindings and filters, including report-only runs."""
    manifest = {}
    filter_rows = []
    def note(binding, page, scope, description, visual_id=""):
        if not binding.get("field"):
            return
        ref = dict(page_ref(report, page), scope=scope, description=description, visualId=visual_id)
        key = (binding.get("table"), binding["field"], binding.get("kind"), binding.get("hierarchy"))
        entry = manifest.setdefault(key, {"table": binding.get("table"), "field": binding["field"],
                                         "kind": binding.get("kind"), "hierarchy": binding.get("hierarchy"),
                                         "runtime": bool(binding.get("runtime")),
                                         "usedIn": [], "locations": []})
        entry["runtime"] = entry["runtime"] and bool(binding.get("runtime"))
        if ref not in entry["locations"]:
            entry["locations"].append(ref)
            entry["usedIn"].append(page_label(ref) + " — " + description)
    for page in report["pages"]:
        page["reference"] = page_ref(report, page)
        page["label"] = page_label(page["reference"])
        for f in report["reportFilters"] + report.get("otherFields", []):
            note(f, page, "report", "Report-level filter or expression")
        for f in page["filters"] + page.get("otherFields", []):
            note(f, page, f.get("level", "page"), f.get("level", "page").capitalize() + " filter or expression")
        for v in page["visuals"]:
            for f in v["fields"]:
                prefix = "Formatting rule in visual: " if f.get("context") == "formatting" else "Visual: "
                note(f, page, "visual", prefix + (v.get("title") or v["type"]), v["id"])
        for f in report["reportFilters"] + page["filters"]:
            filter_rows.append(dict(f, **page_ref(report, page), pageLabel=page["label"]))
    if not report["pages"]:
        for f in report["reportFilters"] + report.get("otherFields", []):
            note(f, None, "report", "Report-level filter or expression; no page resolved")
        filter_rows.extend(dict(f, **page_ref(report), pageLabel=page_label(page_ref(report))) for f in report["reportFilters"])
    for bookmark in report["bookmarks"]:
        for f in bookmark["fields"]:
            note(f, None, "bookmark", "Bookmark: " + bookmark["name"] + " (page not resolved)")
    report["manifest"] = sorted(manifest.values(), key=lambda e: (e["table"] or "", e["field"], e["kind"] or "", e["hierarchy"] or ""))
    report["filterRows"] = filter_rows
    return report


def sync_page_usage(model, report, linked, columns):
    """Publish page-resolved analysis to legacy renderers without label parsing."""
    if not report or not columns:
        return
    table_rows = columns["tablePages"]
    column_rows = {}
    for row in columns["rows"]:
        column_rows.setdefault((row["table"], row["column"]), []).append(row)
    for table in model["tables"]:
        for column in table["columns"]:
            refs = column_rows.get((table["name"], column["name"]), [])
            usage = " ".join(r["pageUsage"] for r in refs)
            column["pageUsage"] = [{k: r[k] for k in ("report", "page", "pageId", "pageUsage", "pageScope")} for r in refs]
            column["reportUsage"] = ("direct" if "Direct" in usage else "via measures" if "Via measures" in usage
                                     else "via calculations" if "Via calculations" in usage
                                     else "possible" if "Possible" in usage
                                     else "report scope only" if any(r["usedInReport"] == "Yes" for r in refs)
                                     else "internal only" if any(r["modelDependencies"] for r in refs) else "none")
    for measure in model["measures"]:
        refs = [r for r in columns["measurePages"]
                if r["table"] == measure["table"] and r["measure"] == measure["name"]]
        measure["pageUsage"] = refs
        measure["usedInReport"] = bool(refs)
        measure["usedBy"] = [page_label(r) if r["pageId"] else
                             f"{r['report']} / {r['usage']}" for r in refs]
    if not linked:
        return
    for usage in linked["tableUsage"]:
        refs = [r for r in table_rows if r["table"] == usage["table"]]
        usage["pageUsage"] = refs
        usage["whereUsed"] = [page_label(r) for r in refs if r["pageId"]]
        # The aggregate label remains a report summary. Page rows carry their
        # own verdict; never stamp the aggregate verdict onto every page.
        kinds = {k for r in refs for k in r["kinds"]}
        usage["usage"] = ("direct" if "Direct" in kinds else "via measures" if
                          kinds & {"Via measures", "Via calculations", "Table expression"}
                          else "possible" if any(k.startswith("Possible") for k in kinds) else "none")
    for row in linked["lineage"]:
        refs = [r for r in table_rows if r["table"] == row["table"] and r["pageId"]]
        row["pageUsage"] = refs
        row["consumers"] = [page_label(r) for r in refs]
        row["usage"] = next(u["usage"] for u in linked["tableUsage"] if u["table"] == row["table"])
    linked["tablePages"] = table_rows


def page_feed_rows(payload, page):
    """A shared per-page feed list, including transitive measure dependencies."""
    columns = payload.get("columns")
    if columns:
        return [{"table": r["table"], "fields": r["fields"], "usage": r["usage"]}
                for r in columns["tablePages"] if r["pageId"] == page["id"]]
    report = payload["report"]
    fields = list(report["reportFilters"]) + report.get("otherFields", []) + list(page["filters"]) + page.get("otherFields", [])
    fields += [f for v in page["visuals"] for f in v["fields"]]
    by_table = {}
    for field in fields:
        by_table.setdefault(field.get("table") or "?", set()).add(field.get("field") or "?")
    return [{"table": t, "fields": sorted(fs), "usage": "Unresolved report binding"}
            for t, fs in sorted(by_table.items())]
