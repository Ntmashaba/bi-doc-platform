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
    # -Password (ConvertTo-SecureString …): the literal inside is the secret, the parenthesis is not (a regression
    # in d37c76b redacted "(ConvertTo-SecureString" and left the password)
    ("New-LocalUser -Name u -Password (ConvertTo-SecureString 'Sup3rSecret' -AsPlainText -Force)",
     f"New-LocalUser -Name u -Password (ConvertTo-SecureString '{REDACTED}' -AsPlainText -Force)"),
    ('Import-PfxCertificate -FilePath c.pfx -Password (ConvertTo-SecureString -String "Sup3rSecret" -Force -AsPlainText)',
     f'Import-PfxCertificate -FilePath c.pfx -Password (ConvertTo-SecureString -String "{REDACTED}" -Force -AsPlainText)'),
    ("Invoke-Sqlcmd -Password:(ConvertTo-SecureString 'Sup3rSecret' -AsPlainText)",
     f"Invoke-Sqlcmd -Password:(ConvertTo-SecureString '{REDACTED}' -AsPlainText)"),
])
def test_redacted_once_and_idempotent(text, expected):
    assert scrub_text(text) == (expected, 1)
    assert scrub_text(expected) == (expected, 0)


@pytest.mark.parametrize("text", [
    "Invoke-Sqlcmd -Password $pw -Query x",                  # a variable names where the secret is
    "Invoke-Sqlcmd -Password -Query x",                      # no value: the next parameter is not a secret
    "New-LocalUser -Name u -Password (Read-Host -AsSecureString)",   # an expression, not a secret
    "Invoke-Thing -Password @($a, $b)",
    "Invoke-Thing -Password [SecureString]$s",
    "Invoke-Thing -Password { Get-Secret }",
    "New-LocalUser -Name u -Password (ConvertTo-SecureString $enc)",  # no literal, no -AsPlainText
    "New-LocalUser -Name u -PasswordNeverExpires",
    "Get-Thing -PasswordFile C:\\p.txt",
    "sqlcmd -S s -E\nSet-Location -Path 'C:\\jobs'",           # a continuation needs a backtick or caret
])
def test_untouched(text):
    assert scrub_text(text) == (text, 0)
