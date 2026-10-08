"""What kind of page a report page is: an ordinary report page, a tooltip page or a drillthrough page.

Read from the page settings the file holds, never from a page's name or size (UI rework, Change 4, Report view).

PBIR (definition/pages/<page>/page.json)
    ``type`` ("Tooltip" | "Drillthrough", schema 1.3.0 and later) and ``pageBinding.type`` ("Default" | "Tooltip" |
    "Drillthrough", every schema version). The schema describes both as the page's specific usage, so a page.json
    with neither describes an ordinary page.

Legacy report layout (a PBIP report.json, a PBIX Layout, pbi-tools' Report/sections/<page>/)
    The page's ``config``: ``type: 1`` marks a tooltip page; no other value has been seen. A drillthrough page is a
    page with drillthrough fields: page filters whose ``howCreated`` is 5, the sixth value of the list PBIR names
    (Auto, User, Drill, Include, Exclude, Drillthrough). Power BI writes a config for every page (all 263 pages of
    the 29 sample reports have one, empty or not); a page whose config has no ``type`` and that has no drillthrough
    fields is an ordinary page.

When the page settings are not in the file (a legacy page with no config) the type is not recorded: no
``pageType`` is written, and the document says "page type not recorded" rather than calling it an ordinary page.
A value the reader does not know is kept as the file writes it (``pageType`` "other" with ``pageTypeRaw``).
"""
from __future__ import annotations

PAGE, TOOLTIP, DRILLTHROUGH, OTHER = "page", "tooltip", "drillthrough", "other"
# How a filter was created, in the legacy layout (a number) and in PBIR (a name).
DRILLTHROUGH_CREATED = (5, "Drillthrough")


def is_drillthrough_filter(raw: dict) -> bool:
    """A filter created by drillthrough: on a drillthrough page, a drillthrough field; on a tooltip page, a
    tooltip field."""
    value = raw.get("howCreated") if isinstance(raw, dict) else None
    return not isinstance(value, bool) and value in DRILLTHROUGH_CREATED


def _has_drillthrough_fields(filters) -> bool:
    return any(f.get("drillthrough") for f in filters or [] if isinstance(f, dict))


def pbir(page_json: dict, page_filters=()) -> dict:
    """The page type of a PBIR page.json, as the fields to add to the page."""
    usage = page_json.get("type")
    binding = page_json.get("pageBinding")
    bound = binding.get("type") if isinstance(binding, dict) else None
    for value in (usage, bound):
        if value == "Tooltip":
            return {"pageType": TOOLTIP}
        if value == "Drillthrough":
            return {"pageType": DRILLTHROUGH}
    if usage is not None:
        return {"pageType": OTHER, "pageTypeRaw": f"type {usage}"}
    if bound not in (None, "Default"):
        return {"pageType": OTHER, "pageTypeRaw": f"pageBinding type {bound}"}
    if _has_drillthrough_fields(page_filters):
        return {"pageType": DRILLTHROUGH}
    return {"pageType": PAGE}


def legacy(config, page_filters=()) -> dict:
    """The page type of a page in the legacy layout, from its config (None when the file has none)."""
    drill = _has_drillthrough_fields(page_filters)
    if not isinstance(config, dict):
        # No page settings in the file: drillthrough fields still prove a drillthrough page; nothing else is known.
        return {"pageType": DRILLTHROUGH} if drill else {}
    kind = config.get("type")
    if kind == 1 and not isinstance(kind, bool):
        return {"pageType": TOOLTIP}
    if kind is not None:
        return {"pageType": OTHER, "pageTypeRaw": f"type {kind}"}
    return {"pageType": DRILLTHROUGH if drill else PAGE}


LABELS = {PAGE: "report page", TOOLTIP: "tooltip page", DRILLTHROUGH: "drillthrough page"}
NOT_RECORDED = "page type not recorded"


def label(page: dict) -> str:
    """The page type in words, for the Word and agent outputs: as the HTML document says it."""
    kind = page.get("pageType")
    if kind in LABELS:
        return LABELS[kind]
    if kind == OTHER:
        return f"page type not recognised ({page.get('pageTypeRaw') or 'no value'})"
    return NOT_RECORDED


def flags(page: dict) -> list[str]:
    """Everything that sets a page apart from an ordinary visible page, in words."""
    out = [] if page.get("pageType") == PAGE else [label(page)]
    if page.get("hidden"):
        out.append("hidden")
    return out
