"""Data sources declared by an Analysis Services model (model.bim or a TMSL script, compatibility level 1200+).

TMSL has two kinds:

* a **provider** ("legacy") data source carries a connection string, and partitions read it with a SQL query;
* a **structured** data source (1400 and later) carries ``connectionDetails`` (a protocol and an address), and
  partitions are Power Query that names the data source: ``Source = #"SQL/server;database"``.

Only identity is read: the kind of source, server, database, path or URL. Credentials, accounts and options are
never copied. A structured data source is exposed to the M tracer as the Power Query it stands for, so a
partition that names it is traced exactly like one that calls the connector itself.

The ``tds`` protocol is checked against Microsoft's sample models. The other protocols follow the same documented
shape but have not been checked against a real file.
"""
from __future__ import annotations

from . import connection_strings as cs
from .legacy_mashup import is_mashup_source
from .source_labels import refine_source_type

# OLE DB / .NET provider name fragment -> source type, in the vocabulary the M connectors use.
_PROVIDERS = (
    ('sqlncli', 'SQL Server'), ('sqloledb', 'SQL Server'), ('msoledbsql', 'SQL Server'), ('sqlclient', 'SQL Server'),
    ('oraoledb', 'Oracle'), ('msdaora', 'Oracle'), ('oracleclient', 'Oracle'), ('oracle', 'Oracle'),
    ('tdoledb', 'Teradata'), ('teradata', 'Teradata'),
    ('msolap', 'Analysis Services'),
    ('msdasql', 'ODBC'),
)
# Structured protocol -> (source type, M function taking server [, database]).
_DATABASES = {
    'tds': ('SQL Server', 'Sql.Database', 'Sql.Databases'),
    'oracle': ('Oracle', None, 'Oracle.Database'),
    'teradata': ('Teradata', None, 'Teradata.Database'),
    'analysis-services': ('Analysis Services', 'AnalysisServices.Database', 'AnalysisServices.Databases'),
}
# Structured protocol -> (source type, M function taking one location, address key).
_LOCATIONS = {
    'file': ('File', 'File.Contents', 'path'),
    'folder': ('Folder', 'Folder.Files', 'path'),
    'http': ('Web', 'Web.Contents', 'url'),
}
_ODBC_KEYS = ('dsn', 'driver', 'server', 'dbq', 'data source', 'database', 'initial catalog')


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ''


def _m_string(value: str) -> str:
    # "" escapes a quote; #(#)( keeps a literal "#(" from being read as an M escape sequence.
    return '"' + value.replace('#(', '#(#)(').replace('"', '""') + '"'


def provider_source_type(connection_string) -> str | None:
    """SQL Server, Oracle ... from a connection string's Provider, or None when it does not say."""
    provider = (cs.values(connection_string).get('provider') or '').lower()
    return next((kind for fragment, kind in _PROVIDERS if fragment in provider), None)


def _details(ds: dict) -> tuple[str, dict]:
    details = ds.get('connectionDetails') if isinstance(ds.get('connectionDetails'), dict) else {}
    address = details.get('address') if isinstance(details.get('address'), dict) else {}
    return _text(details.get('protocol')).lower(), address


def is_structured(ds: dict | None) -> bool:
    return bool(ds) and (str(ds.get('type') or '').lower() == 'structured' or 'connectionDetails' in ds)


def source_type(ds: dict | None) -> str | None:
    """The source type of a data source, or None when its provider or protocol is not recognised."""
    if not ds:
        return None
    if is_structured(ds):
        protocol, _ = _details(ds)
        if protocol == 'odbc':
            return 'ODBC'
        return (_DATABASES.get(protocol) or _LOCATIONS.get(protocol) or (None,))[0]
    return provider_source_type(ds.get('connectionString'))


def equivalent_m(ds: dict) -> str | None:
    """Power Query that opens the same source as a structured data source, or None when it cannot be written."""
    if not is_structured(ds):
        return None
    protocol, address = _details(ds)
    call = None
    if protocol in _DATABASES:
        _, with_database, server_only = _DATABASES[protocol]
        server, database = _text(address.get('server')), _text(address.get('database'))
        if server and database and with_database:
            call = f'{with_database}({_m_string(server)}, {_m_string(database)})'
        elif server:
            call = f'{server_only}({_m_string(server)})'
    elif protocol in _LOCATIONS:
        _, function, key = _LOCATIONS[protocol]
        location = cs.strip_userinfo(_text(address.get(key)))      # no user:password@, as in `describe`
        if location:
            call = f'{function}({_m_string(location)})'
    elif protocol == 'odbc':
        options = address.get('options') if isinstance(address.get('options'), dict) else {}
        named = {str(k).lower(): _text(v) for k, v in options.items()}
        parts = [f'{key}={{{named[key]}}}' if ';' in named[key] else f'{key}={named[key]}'
                 for key in _ODBC_KEYS if named.get(key)]
        if parts:
            call = f'Odbc.DataSource({_m_string(";".join(parts))})'
    if call is None:
        return None
    return f'// Structured data source (protocol {protocol}), shown as the Power Query it stands for\n{call}'


_AUTHENTICATION_KINDS = {
    'usernamepassword': 'User name and password', 'windows': 'Windows', 'serviceaccount': 'Service account',
    'oauth2': 'OAuth2 (organisational account)', 'key': 'Account key', 'anonymous': 'Anonymous', 'implicit': 'Implicit',
    'webapi': 'Web API key', 'parameterized': 'Parameterised'}
_IMPERSONATION = {
    'impersonateserviceaccount': 'Service account (impersonation)', 'impersonateaccount': 'A named Windows account (impersonation)',
    'impersonatecurrentuser': 'The current user (impersonation)', 'impersonateanonymous': 'Anonymous (impersonation)',
    'impersonateunattendedaccount': 'Unattended account (impersonation)'}
# TOM ImpersonationMode as the metadata database numbers it.
_IMPERSONATION_NUMBERS = {2: 'impersonateaccount', 3: 'impersonateanonymous', 4: 'impersonatecurrentuser',
                          5: 'impersonateserviceaccount', 6: 'impersonateunattendedaccount'}
_KIND_NAME = __import__('re').compile(r'[A-Za-z][A-Za-z0-9 _-]{0,40}')


def authentication(ds: dict) -> str | None:
    """How the data source authenticates, when the file says so; None when it does not.

    Only the type is read: the authentication kind of a structured data source's credential, integrated security
    in a provider connection string, or the impersonation mode. An account name, a password or a key is never
    read. A file made by Power BI Desktop normally records none of this (credentials are kept outside the file)."""
    credential = ds.get('credential') if isinstance(ds.get('credential'), dict) else {}
    kind = credential.get('AuthenticationKind') or credential.get('authenticationKind')
    if isinstance(kind, str) and _KIND_NAME.fullmatch(kind.strip()):
        return _AUTHENTICATION_KINDS.get(kind.strip().lower(), kind.strip())
    named = cs.values(ds.get('connectionString'))
    integrated = (named.get('integrated security') or named.get('trusted_connection') or '').strip().lower()
    if integrated in ('sspi', 'true', 'yes'):
        return 'Windows integrated security'
    if named.get('user id') or named.get('uid') or ds.get('signsInWithAccount'):
        return 'User name and password'
    mode = ds.get('impersonationMode')
    if isinstance(mode, int) and not isinstance(mode, bool):
        mode = _IMPERSONATION_NUMBERS.get(mode)
    return _IMPERSONATION.get(str(mode).strip().lower()) if mode else None


def describe(ds: dict) -> dict:
    """Identity of one data source for the normalised model. Never includes credentials."""
    structured = is_structured(ds)
    out = {'name': ds.get('name') or '', 'kind': 'structured' if structured else 'provider',
           'sourceType': source_type(ds), 'server': None, 'database': None, 'location': None,
           'protocol': None, 'provider': None, 'expression': None, 'authentication': authentication(ds)}
    if structured:
        protocol, address = _details(ds)
        out.update(protocol=protocol or None, server=_text(address.get('server')) or None,
                   database=_text(address.get('database')) or None,
                   location=cs.strip_userinfo(_text(address.get('path')) or _text(address.get('url'))) or None,
                   expression=equivalent_m(ds))
    else:
        named = cs.values(ds.get('connectionString'))
        out.update(provider=named.get('provider') or None,
                   server=next((named[k] for k in ('data source', 'server', 'address', 'addr', 'network address')
                                if named.get(k)), None),
                   database=next((named[k] for k in ('initial catalog', 'database') if named.get(k)), None))
    if out['sourceType']:
        out['sourceType'] = refine_source_type(out['sourceType'], out['location'])
    return out


def describe_all(model: dict) -> list[dict]:
    """Every data source except the pre-2019 Power BI mashup packages, which `legacy_mashup` inlines."""
    return [describe(d) for d in model.get('dataSources') or [] if isinstance(d, dict) and not is_mashup_source(d)]
