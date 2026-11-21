"""The redaction safety net: every secret form a job step scripts, exact counts, idempotent, no false positives."""

from __future__ import annotations

import pytest

from sqlagentdocgen.redact import REDACTED, scrub_text

SECRET = "Sup3rSecret!"
LEAKING = [
    "CREATE LOGIN x WITH PASSWORD = N'Sup3rSecret!', CHECK_POLICY = OFF",
    "OPEN MASTER KEY DECRYPTION BY PASSWORD = 'Sup3rSecret!'",
    "CREATE CERTIFICATE c ENCRYPTION BY PASSWORD = N'Sup3rSecret!' WITH SUBJECT = N'x'",
    "EXEC sp_addlinkedsrvlogin @rmtsrvname = N'R', @useself = N'False', @rmtpassword = N'Sup3rSecret!'",
    "EXEC sp_add_proxy @proxy_name = N'p', @proxy_password = N'Sup3rSecret!'",
    "sqlcmd -S srv -U u -P Sup3rSecret! -Q x",
    "sqlcmd -S srv -U u -PSup3rSecret! -Q x",
    'sqlcmd -S srv -U u -P"Sup3rSecret!" -Q x',
    "dtexec /F p.dtsx /DECRYPT Sup3rSecret!",
    'dtexec /F p.dtsx /De "Sup3rSecret! with spaces"',
    "$p = ConvertTo-SecureString 'Sup3rSecret!' -AsPlainText -Force",
    '$p = ConvertTo-SecureString -String "Sup3rSecret!" -AsPlainText -Force',
    "$p = ConvertTo-SecureString -AsPlainText -Force -String 'Sup3rSecret!'",
    "Server=a;Password=Sup3rSecret! with, commas;Database=d",
    "<Password>Sup3rSecret!</Password>",
    '{"password": "Sup3rSecret!"}',
]


@pytest.mark.parametrize("text", LEAKING)
def test_the_secret_is_gone_and_counted_once(text):
    out, n = scrub_text(text)
    assert "Sup3rSecret" not in out, out
    assert n == 1, (out, n)
    assert scrub_text(out) == (out, 0)                 # a second pass changes and counts nothing


@pytest.mark.parametrize("text", [
    "@rmtpassword = @pw, @useself = N'False'",          # a variable names where the secret is; it is not one
    "ConvertTo-SecureString $p -AsPlainText -Force",
    "ConvertTo-SecureString 'encrypted-standard-string'",  # without -AsPlainText it is not the plain text
    "passwordHint = N'keep'",
    "CHECK_POLICY = OFF",
    "sqlcmd -S srv -E -Q x",
    "robocopy C:\\a D:\\b /DCOPY:T",
    "<PasswordPolicy>keep</PasswordPolicy>",
])
def test_nothing_that_is_not_a_secret_is_touched(text):
    assert scrub_text(text) == (text, 0)


def test_the_rest_of_the_statement_survives():
    out, _ = scrub_text("CREATE LOGIN x WITH PASSWORD = N'a''b', CHECK_POLICY = OFF")
    assert out == f"CREATE LOGIN x WITH PASSWORD = {REDACTED}, CHECK_POLICY = OFF"
    out, _ = scrub_text("sqlcmd -S srv -PSup3rSecret! -Q \"SELECT 1\"")
    assert out == f"sqlcmd -S srv -P{REDACTED} -Q \"SELECT 1\""
