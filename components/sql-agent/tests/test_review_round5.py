"""Review round 5: PowerShell -Password, continued sql-tool lines. Synthetic only."""

from __future__ import annotations

import pytest

from sqlagentdocgen.redact import REDACTED, scrub_text


@pytest.mark.parametrize("text, expected", [
    ("Invoke-Sqlcmd -ServerInstance s -Username u -Password 'Sup3rSecret' -Query 'SELECT 1'",
     f"Invoke-Sqlcmd -ServerInstance s -Username u -Password {REDACTED} -Query 'SELECT 1'"),
    ('Invoke-Sqlcmd -Password "Sup3rSecret" -Query x', f"Invoke-Sqlcmd -Password {REDACTED} -Query x"),
    ("Invoke-Sqlcmd -Password:Sup3rSecret -Query x", f"Invoke-Sqlcmd -Password:{REDACTED} -Query x"),
    ("Invoke-Sqlcmd -password Sup3rSecret", f"Invoke-Sqlcmd -password {REDACTED}"),
    ("sqlcmd -S s -U u `\n  -PSup3rSecret `\n  -Q x", f"sqlcmd -S s -U u `\n  -P{REDACTED} `\n  -Q x"),
    ("bcp t out f ^\n  -S s -U u ^\n  -PSup3rSecret", f"bcp t out f ^\n  -S s -U u ^\n  -P{REDACTED}"),
])
def test_redacted_once_and_idempotent(text, expected):
    assert scrub_text(text) == (expected, 1)
    assert scrub_text(expected) == (expected, 0)


@pytest.mark.parametrize("text", [
    "Invoke-Sqlcmd -Password $pw -Query x",                  # a variable names where the secret is
    "Invoke-Sqlcmd -Password -Query x",                      # no value: the next parameter is not a secret
    "New-LocalUser -Name u -PasswordNeverExpires",
    "Get-Thing -PasswordFile C:\\p.txt",
    "sqlcmd -S s -E\nSet-Location -Path 'C:\\jobs'",           # a continuation needs a backtick or caret
])
def test_untouched(text):
    assert scrub_text(text) == (text, 0)
