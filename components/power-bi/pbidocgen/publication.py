"""The text a shared publication leaves where it withheld or cleaned something.

The engine never withholds anything itself: the platform projects a payload before rendering it
(`bidoc_engines.projection`, docs/contracts/projection-v1.md). The document only needs to recognise the result,
so that it can say "withheld" where a script is missing instead of showing the marker as if it were code. A test
in packages/engines keeps these equal to the projection's own constants.
"""
CODE_WITHHELD = "[query code withheld]"
CLEANED = ("[credential withheld]", "[entered data withheld]", "personal location withheld [ref ")
PUBLICATION = ("included", "cleaned", "withheld")


def publication_of(code: str | None) -> str:
    """How a script reached this document: included as written, included after cleaning, or withheld."""
    text = code or ""
    if text.strip() == CODE_WITHHELD:
        return "withheld"
    return "cleaned" if any(marker in text for marker in CLEANED) else "included"
