"""Is a hand-entered value a credential rather than a reference to one?

Documentation details are entered by people (report location, connection accounts, owner, runbook, notes) and
must hold references only: an account name, an authentication type, where a secret lives. This module decides,
for both engines, when a value carries the secret itself, so the details are refused before any document is
written, in either profile. It is shared by copy: pbidocgen/credential_values.py and
adfdocgen/credential_values.py are identical, and packages/engines/tests/test_generation_details.py fails if they
differ. Edit both.

Refused:
  * a credential setting with a value: Password=…, pwd=…, client_secret=…, api_key=…, AccountKey=…, token=…
  * a credential in a URL query: ?sig=…, &token=…, ?api_key=…, ?code=…
  * a password in a URL: scheme://user:password@host (a user name alone, user@host, is an account reference)
  * an Authorization header value, or a bearer token: Authorization: Basic …, Bearer eyJ0eXAi…
Kept: "svc-pbi-refresh", "Windows", "OAuth2 (password stored in Key Vault)", a Key Vault URL or
@Microsoft.KeyVault(SecretUri=…) reference, "Bearer authentication", "Rotate the client secret quarterly".
"""
from __future__ import annotations

import re

_NAMES = (r"password|passwd|pwd|passphrase|secret|client_secret|clientsecret|accountkey|account_key|accesskey|"
          r"access_key|sharedaccesskey|sharedaccesssignature|sig|token|access_token|refresh_token|id_token|apikey|"
          r"api_key|api-key|subscription-key|privatekey|private_key")
_SETTING = re.compile(rf"(?i)(?<![a-z0-9])(?:{_NAMES})\s*=\s*[^\s;&'\"]")
_QUERY = re.compile(r"(?i)[?&](?:sig|signature|token|access_token|code|key|apikey|api_key|api-key|password|pwd|"
                    r"secret|client_secret|subscription-key|auth)=[^&#\s]")
_URL_PASSWORD = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")
_AUTHORIZATION = re.compile(r"(?i)\bauthorization\s*[:=]\s*\S")
_BEARER = re.compile(r"(?i)\bbearer\s+(?:(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{8,}|[A-Za-z0-9._~+/=-]{20,})")
_PATTERNS = (_SETTING, _QUERY, _URL_PASSWORD, _AUTHORIZATION, _BEARER)


def looks_like_credential(text: str) -> bool:
    """True when `text` appears to contain a secret value rather than a reference to one."""
    return isinstance(text, str) and any(p.search(text) for p in _PATTERNS)
