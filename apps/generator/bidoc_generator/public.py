"""What the generator says about a failure, in batch history, `--json` output and the desktop app.

Messages written for people (an input that is not supported, a pairing that names a missing model, a prerequisite
that is missing) are kept, but filtered with the shared-publication cleaner first: a personal location (under a user
profile or home folder) keeps only its file name and a stable reference, and a credential in a connection string, URL
or header is withheld. An error nobody wrote a message for gets a fixed message naming only its type: its own text can
quote a path, a value or a secret.
"""
from __future__ import annotations


def public_text(text) -> str:
    from bidoc_engines.projection import clean_string, withhold_personal_paths  # noqa: PLC0415
    if not isinstance(text, str) or not text:
        return text
    cleaned, _ = clean_string(withhold_personal_paths(text))
    return cleaned


def public_errors(errors) -> list:
    """Errors (and warnings) as they may be shown or stored: every message filtered, everything else kept."""
    out = []
    for entry in errors or []:
        if isinstance(entry, dict):
            out.append({**entry, "message": public_text(entry.get("message", ""))})
        else:
            out.append(public_text(entry))
    return out


def unexpected(exc: BaseException) -> str:
    """A failure the generator has no message for: its type only, and what to do."""
    return (f"Documenting this input stopped on an unexpected error ({type(exc).__name__}). The other inputs are not "
            f"affected. Retry it; if it happens again, run 'bidoc doctor' and report the error type.")
