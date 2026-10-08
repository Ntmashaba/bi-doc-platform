"""Static M source tracing. Never evaluates M, SQL, credentials or network calls.

Supported: let bindings, literal parameters/concatenation, shared references,
connector calls, navigation records, native SQL and common table transforms.
Unsupported forms retain conservative dependencies and explicit diagnostics.
"""
from __future__ import annotations
import copy
import re
from dataclasses import dataclass, field
from .sql_sources import extract_sql_objects
from .external_sources import EXTERNAL, READERS, external_value, navigate_external


@dataclass
class Token:
    """value: the decoded text. kind: id, string or symbol. start/end: where it is in the source, so a caller
    can quote the source exactly as written. quoted: an identifier written #"...", which is never a keyword
    (a step may be named #"in")."""
    value: str
    kind: str
    start: int = 0
    end: int = 0
    quoted: bool = False


def decode_m(value):
    value = value.replace('""', '"')
    def replace(match):
        result = ''
        for code in match[1].split(','):
            code = code.strip()
            if code in {'lf', 'cr', 'tab', '#'}:
                result += {'lf': '\n', 'cr': '\r', 'tab': '\t', '#': '#'}[code]
            elif re.fullmatch(r'[0-9a-fA-F]{4}|[0-9a-fA-F]{8}', code):
                point = int(code, 16)
                if 0xD800 <= point <= 0xDFFF:
                    raise ValueError('Isolated surrogate M escape')
                result += chr(point)
            else:
                raise ValueError('Unsupported M escape: #(' + code + ')')
        return result
    return re.sub(r'#\(([^)]+)\)', replace, value)


def tokenize(text):
    result, i = [], 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        if text.startswith('//', i):
            end = text.find('\n', i)
            i = len(text) if end < 0 else end
            continue
        if text.startswith('/*', i):
            depth, i = 1, i + 2
            while depth and i < len(text):
                if text.startswith('/*', i):
                    depth, i = depth + 1, i + 2
                elif text.startswith('*/', i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            if depth:
                raise ValueError('Unterminated M comment')
            continue
        quoted_id = text.startswith('#"', i)
        if quoted_id or text[i] == '"':
            opened = i
            i += 2 if quoted_id else 1
            start, closed = i, False
            while i < len(text):
                if text[i] == '"':
                    if i + 1 < len(text) and text[i + 1] == '"':
                        i += 2
                        continue
                    closed = True
                    break
                i += 1
            if not closed:
                raise ValueError('Unterminated M string/identifier')
            result.append(Token(decode_m(text[start:i]), 'id' if quoted_id else 'string', opened, i + 1, quoted_id))
            i += 1
            continue
        match = _IDENTIFIER.match(text, i)
        if match:
            result.append(Token(match[0], 'id', i, match.end()))
            i = match.end()
        else:
            result.append(Token(text[i], 'symbol', i, i + 1))
            i += 1
    return result


_IDENTIFIER = re.compile(r'[^\W\d][\w.]*', re.UNICODE)


def keyword(token, value) -> bool:
    """True when the token is the keyword `value` as written, not an identifier or text that spells it."""
    return token.kind == 'id' and not token.quoted and token.value == value


def pairs_for(ts):
    stack, pairs = [], {}
    for i, t in enumerate(ts):
        if t.kind != 'symbol':
            continue
        if t.value in '([{':
            stack.append(i)
        elif t.value in ')]}':
            if not stack or '([{'.index(ts[stack[-1]].value) != ')]}'.index(t.value):
                raise ValueError('Unbalanced M delimiters')
            pairs[stack.pop()] = i
    if stack:
        raise ValueError('Unbalanced M delimiters')
    return pairs


def top_positions(ts, delimiter):
    depth, lets = 0, 0
    for i, t in enumerate(ts):
        if t.kind == 'symbol' and t.value in '([{':
            depth += 1
        elif t.kind == 'symbol' and t.value in ')]}':
            depth -= 1
        elif not depth:
            if keyword(t, 'let'):
                lets += 1
            elif keyword(t, 'in'):
                lets -= 1
            elif not lets and t.value == delimiter and t.kind != 'string' and not t.quoted:
                yield i


def split(ts, delimiter=','):
    result, start = [], 0
    for i in top_positions(ts, delimiter):
        result.append(ts[start:i])
        start = i + 1
    result.append(ts[start:])
    return result


@dataclass
class Value:
    text: str | None = None
    kind: str = 'unknown'
    connections: list = field(default_factory=list)
    objects: list = field(default_factory=list)
    members: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)
    references: dict = field(default_factory=dict)
    effects: list = field(default_factory=list)


def union(values, issue=None):
    result = Value(kind='table')
    for value in values:
        result.connections += copy.deepcopy(value.connections)
        result.objects += copy.deepcopy(value.objects)
        result.issues += value.issues
        result.references.update(value.references)
        result.effects += value.effects
    if issue:
        result.issues.append(issue)
    return result


CONNECTORS = {'Sql.Database': 'SQL Server', 'Sql.Databases': 'SQL Server',
              'Oracle.Database': 'Oracle', 'Teradata.Database': 'Teradata',
              'Odbc.DataSource': 'ODBC', 'Odbc.Query': 'ODBC',
              # Names shared with model_parser's patterns, so both paths agree.
              'Snowflake.Databases': 'Snowflake', 'Databricks.Catalogs': 'Databricks',
              'Databricks.Query': 'Databricks', 'Databricks.Contents': 'Databricks',
              'AzureSql.Database': 'Azure Synapse / SQL', 'AzureSql.Databases': 'Azure Synapse / SQL',
              'AnalysisServices.Database': 'Analysis Services', 'AnalysisServices.Databases': 'Analysis Services'}
TRANSFORMS = {'Table.SelectRows', 'Table.SelectColumns', 'Table.RemoveColumns', 'Table.RenameColumns',
              'Table.TransformColumnTypes', 'Table.TransformColumns', 'Table.ReorderColumns',
              'Table.Sort', 'Table.Distinct', 'Table.Buffer', 'Table.FirstN', 'Table.LastN',
              'Table.Skip', 'Table.PromoteHeaders', 'Table.DemoteHeaders', 'Table.ReplaceValue',
              'Table.ExpandTableColumn', 'Table.ExpandRecordColumn', 'Table.RemoveRowsWithErrors',
              'Table.ReplaceErrorValues', 'Table.AddIndexColumn', 'Table.Unpivot', 'Table.UnpivotOtherColumns',
              # Row-level reshaping: the rows still come from the first argument.
              'Table.AddColumn', 'Table.DuplicateColumn', 'Table.SplitColumn', 'Table.CombineColumns',
              'Table.Group', 'Table.Pivot', 'Table.FillDown', 'Table.FillUp', 'Table.Transpose',
              'Table.RemoveRows', 'Table.RemoveFirstN', 'Table.RemoveLastN', 'Table.Range',
              'Table.AlternateRows', 'Table.RemoveMatchingRows', 'Table.ExpandListColumn',
              'Table.AddKey', 'Table.RenameColumns', 'Table.TransformColumnNames', 'Table.Repeat'}
COMBINES = {'Table.Combine', 'Table.Join', 'Table.NestedJoin'}
DATAFLOWS = {'PowerBI.Dataflows': 'Power BI dataflow', 'PowerPlatform.Dataflows': 'Power Platform dataflow'}
# Data typed into Power Query ("Enter data") or built from literals: no external source.
ENTERED = {'#table', 'Table.FromRows', 'Table.FromRecords', 'Table.FromColumns', 'Table.FromValue'}
# Values generated inside Power Query (date lists, number ranges).
GENERATORS = {'List.Dates', 'List.DateTimes', 'List.Numbers', 'List.Generate', 'List.Times', 'List.Durations'}
FROM_LIST = {'Table.FromList'}
# Constants written as constructors, such as an incremental-refresh RangeStart of #datetime(2024, 1, 1, 0, 0, 0).
SCALAR_CONSTRUCTORS = {'#date', '#datetime', '#datetimezone', '#time', '#duration'}
# Standard-library namespaces: a call into one of these is never a data connector.
LIBRARY = {'Table', 'List', 'Text', 'Number', 'Date', 'DateTime', 'DateTimeZone', 'Duration', 'Time',
           'Record', 'Value', 'Binary', 'BinaryFormat', 'Json', 'Csv', 'Xml', 'Excel', 'Splitter', 'Combiner',
           'Replacer', 'Comparer', 'Lines', 'Uri', 'Expression', 'Function', 'Type', 'Logical', 'Byte',
           'Int8', 'Int16', 'Int32', 'Int64', 'Single', 'Double', 'Decimal', 'Currency', 'Percentage',
           'Character', 'Culture', 'Order', 'JoinKind', 'JoinAlgorithm', 'MissingField', 'Occurrence',
           'QuoteStyle', 'RoundingMode', 'ExtraValues', 'Day', 'Precision', 'Error', 'Diagnostics',
           'Cube', 'Action', 'Variable', 'Embedded', 'Graph', 'Pdf', 'Html', 'Access', 'Web', 'Folder',
           'File', 'SharePoint', 'OData', 'AzureStorage', 'Sql', 'Oracle', 'Teradata', 'Odbc',
           'PowerBI', 'PowerPlatform', 'Json', 'Parquet', 'Compression', 'BinaryEncoding',
           'TextEncoding', 'Guid', 'Password', 'RelativePosition', 'Resource', 'SapBusinessWarehouse'}
INTERNAL_SOURCES = {'Entered data', 'Generated in Power Query'}
# Library namespaces that can read data. Their connectors and readers are
# handled explicitly; anything else in these namespaces stays a coverage gap.
DATA_NAMESPACES = {'Web', 'File', 'Folder', 'SharePoint', 'OData', 'AzureStorage', 'Sql', 'Oracle', 'Teradata',
                   'Odbc', 'PowerBI', 'PowerPlatform', 'Access', 'Excel', 'Csv', 'Json', 'Xml', 'Pdf', 'Html',
                   'Parquet', 'Cube', 'SapBusinessWarehouse', 'Embedded', 'Graph', 'Resource', 'Variable', 'Action',
                   'Expression'}


def pure_library(name: str) -> bool:
    """Text.PadStart, Splitter.SplitTextByDelimiter, QuoteStyle.Csv: computed from
    their arguments (or constants); they read no data."""
    namespace = name.split('.', 1)[0] if '.' in name else ''
    return namespace in LIBRARY and namespace not in DATA_NAMESPACES


# Library functions that return metadata or scalars computed from their inputs
# (column lists, cleaned names). They read no new data; their inputs are traced.
PURE = {'Table.ColumnsOfType', 'Table.ColumnNames', 'Table.Schema', 'Table.RowCount', 'Table.IsEmpty',
        'Text.Clean', 'Text.Trim', 'Text.Upper', 'Text.Lower', 'Text.Proper', 'Text.From', 'Number.From',
        'Date.From', 'DateTime.From', 'List.Distinct', 'List.Select', 'List.Transform', 'List.Combine',
        'List.RemoveNulls', 'List.Contains', 'List.Max', 'List.Min', 'Record.Field', 'Table.Column'}


class Tracer:
    def __init__(self, definitions=None):
        self.definitions = definitions or {}
        self.cache, self.active = {}, set()
        self.current_query = ''

    def named(self, name):
        if name in self.cache:
            return copy.deepcopy(self.cache[name])
        if name in self.active:
            return Value(issues=['Cyclic shared-query reference: ' + name])
        if name in self.definitions and self.definitions[name] is None:
            return Value(issues=['Ambiguous shared-query name: ' + name])
        if name not in self.definitions:
            return Value(issues=['Unresolved M reference: ' + name])
        self.active.add(name)
        value = self.trace(self.definitions[name], name)
        self.active.remove(name)
        value.references[name] = self.definitions[name]
        self.cache[name] = copy.deepcopy(value)
        return value

    def trace(self, code, query_name=''):
        previous = self.current_query
        self.current_query = query_name
        try:
            ts = tokenize(code)
            return self.evaluate(ts, self.named)
        except (ValueError, RecursionError) as exc:
            return Value(issues=['M extraction incomplete: ' + str(exc)])
        finally:
            self.current_query = previous

    def evaluate(self, ts, resolve):
        if not ts:
            return Value(issues=['Empty M expression'])
        pairs = pairs_for(ts)
        if keyword(ts[0], 'let'):
            depth, lets, end = 0, 0, None
            for i, t in enumerate(ts):
                if t.kind == 'symbol' and t.value in '([{':
                    depth += 1
                elif t.kind == 'symbol' and t.value in ')]}':
                    depth -= 1
                elif not depth and t.kind == 'id' and not t.quoted:
                    if t.value == 'let':
                        lets += 1
                    elif t.value == 'in':
                        lets -= 1
                        if lets == 0:
                            end = i
                            break
            if end is None:
                return Value(issues=['M let expression has no matching in'])
            bindings, cache, active = {}, {}, set()
            for item in split(ts[1:end]):
                equals = list(top_positions(item, '='))
                if not equals or equals[0] != 1 or item[0].kind != 'id':
                    return Value(issues=['Unsupported M let binding'])
                if item[0].value in bindings:
                    return Value(issues=['Duplicate M let binding: ' + item[0].value])
                bindings[item[0].value] = item[2:]
            def local(name):
                if name not in bindings:
                    return resolve(name)
                if name in active:
                    return Value(issues=['Cyclic M let reference: ' + name])
                if name not in cache:
                    active.add(name)
                    cache[name] = self.evaluate(bindings[name], local)
                    active.remove(name)
                return copy.deepcopy(cache[name])
            return self.evaluate(ts[end + 1:], local)
        meta = list(top_positions(ts, 'meta'))
        if meta:
            return self.evaluate(ts[:meta[0]], resolve)
        concat = split(ts, '&')
        if len(concat) > 1:
            values = [self.evaluate(part, resolve) for part in concat]
            result = union(values)
            if all(v.kind == 'text' for v in values):
                result.kind, result.text = 'text', ''.join(v.text for v in values)
            else:
                result.issues.append('Dynamic M concatenation cannot be resolved')
            return result
        if ts[0].value == '(' and 0 in pairs and pairs[0] + 2 < len(ts) \
                and ts[pairs[0] + 1].value == '=' and ts[pairs[0] + 2].value == '>':
            # A function definition, (x as binary) => body. Its parameters are
            # inputs supplied at call time; anything the body opens itself
            # (a connector with a literal server) is still a source.
            params = {t.value for t in ts[1:pairs[0]] if t.kind == 'id' and t.value not in {'as', 'optional', 'nullable'}}
            body = self.evaluate(ts[pairs[0] + 3:],
                                 lambda name: Value(kind='literal', text=name) if name in params else resolve(name))
            body.kind, body.text = 'function', 'function'
            return body
        if len(ts) > 1 and all(t.kind not in ('id', 'string') and t.value not in '([{' for t in ts):
            # Literals and operators only (-1, 2 * 3): a constant.
            return Value(text=''.join(t.value for t in ts), kind='literal')
        if ts[0].kind == 'id' and ts[0].value == 'type':
            # A type expression (type table, type text): no data.
            return Value(text=' '.join(t.value for t in ts), kind='literal')
        if ts[0].kind == 'id' and ts[0].value == 'each':
            # A row function (each ...) computes values per row; any query it
            # reads is kept as a possible source. Handled before calls, since
            # "each (...)" would otherwise look like a call to "each".
            return union([c for c in (resolve(t.value) for t in ts[1:] if t.kind == 'id'
                                      and t.value not in {'each', 'if', 'then', 'else', 'true', 'false', 'null', 'and', 'or', 'not'})
                          if c.connections or c.objects])
        if len(ts) == 1:
            if ts[0].kind == 'string':
                return Value(text=ts[0].value, kind='text')
            if ts[0].kind == 'id':
                if ts[0].value in {'true', 'false', 'null'}:
                    return Value(text=ts[0].value, kind='literal')
                if ts[0].value == '_' or ((ts[0].value in PURE or ts[0].value in TRANSFORMS
                                           or pure_library(ts[0].value)) and ts[0].value not in self.definitions):
                    # A library function passed as a value (Text.Clean).
                    return Value(text=ts[0].value, kind='literal')
                return resolve(ts[0].value)
            return Value(text=ts[0].value, kind='literal')
        if 0 in pairs and pairs[0] == len(ts) - 1:
            if ts[0].value == '(':
                return self.evaluate(ts[1:-1], resolve)
            if ts[0].value == '{':
                return union([self.evaluate(item, resolve) for item in split(ts[1:-1]) if item])
            if ts[0].value == '[':
                members = {}
                for item in split(ts[1:-1]):
                    eq = list(top_positions(item, '='))
                    if not item:
                        continue
                    if not eq or not eq[0] or any(t.kind != 'id' for t in item[:eq[0]]):
                        return Value(issues=['Row/record expression is not a static record'])
                    members[' '.join(t.value for t in item[:eq[0]])] = self.evaluate(item[eq[0]+1:], resolve)
                result = union(members.values())
                result.kind, result.members = 'record', members
                return result
        # Peel navigation suffixes from a complete expression, not from a
        # regex match divorced from the connection that produced the value.
        ends = {end: start for start, end in pairs.items()}
        final = ends.get(len(ts) - 1)
        if final is not None and final > 0 and ts[final].value == '[':
            base = self.evaluate(ts[:final], resolve)
            if len(ts[final + 1:-1]) == 1:
                name = ts[final + 1].value
                if name in base.members:
                    member = copy.deepcopy(base.members[name])
                    member.references.update(base.references)
                    return member
                return base
        if final is not None and final > 0 and ts[final].value == '{':
            base = self.evaluate(ts[:final], resolve)
            record = self.evaluate(ts[final + 1:-1], resolve)
            return self.navigate(base, record)
        hashed = (ts[0].kind == 'symbol' and ts[0].value == '#' and len(ts) > 2 and ts[1].kind == 'id'
                  and 2 in pairs and pairs[2] == len(ts) - 1)
        if hashed or (ts[0].kind == 'id' and 1 in pairs and pairs[1] == len(ts) - 1):
            fn = '#' + ts[1].value if hashed else ts[0].value
            arg_tokens = split(ts[3:-1] if hashed else ts[2:-1])
            if fn in SCALAR_CONSTRUCTORS:
                # A date or duration value: it reads no data, like 1 or "text".
                return Value(text=''.join(t.value for t in ts), kind='literal')
            if fn in ENTERED or fn in GENERATORS or fn in FROM_LIST:
                return self.internal(fn, arg_tokens, resolve)
            if fn in TRANSFORMS or fn in READERS:
                first = self.evaluate(arg_tokens[0], resolve)
                # Simple row transformations preserve the input lineage. If an
                # argument calls code, inspect it conservatively for more input.
                extra = []
                for arg in arg_tokens[1:]:
                    # Inspect arguments that call code or name another query.
                    if any(t.kind == 'id' and ((i + 1 < len(arg) and arg[i + 1].value == '(') or t.value in self.definitions)
                           for i, t in enumerate(arg)):
                        extra.append(self.evaluate(arg, resolve))
                result = union([first] + extra)
                if fn in READERS:
                    for conn in result.connections:
                        if conn.get('navigationMode'):
                            conn['navigationMode'] = 'document'
                if fn == 'Table.SelectRows':
                    # Predicate references can influence rows without contributing
                    # displayed columns. Retain these as potential dependencies.
                    for token in arg_tokens[1] if len(arg_tokens)>1 else []:
                        if token.kind == 'id':
                            candidate = resolve(token.value)
                            if candidate.connections or candidate.objects:
                                result = union([result, candidate])
                    selected = self.external_filter(arg_tokens[1], resolve) if len(arg_tokens)>1 else None
                    if selected:
                        refined = navigate_external(result, selected)
                        if refined is not None:
                            result = refined
                    result.effects.append('Row filter; may affect which records reach the model')
                elif fn in {'Table.SelectColumns', 'Table.RemoveColumns'}:
                    result.effects.append('Column selection/removal; surviving source-column contribution not proven')
                if fn in {'Table.AddColumn', 'Table.ExpandTableColumn', 'Table.TransformColumns'}:
                    combine_folder(result)
                return result
            args = [self.evaluate(arg, resolve) for arg in arg_tokens if arg]
            if fn in EXTERNAL:
                return external_value(self, fn, args)
            if fn in CONNECTORS:
                return self.connector(fn, args)
            if fn in DATAFLOWS:
                conn = dict(sourceType=DATAFLOWS[fn], server='', database='', schema='',
                            primaryQuery=self.current_query, navigationMode='dataflow')
                result = union(args)
                result.connections, result.objects, result.kind = [conn], [], 'connection'
                return result
            if fn == 'Value.NativeQuery':
                if len(args) < 2:
                    return Value(issues=['Value.NativeQuery lacks a target or SQL expression'])
                return self.sql(args[0], args[1])
            if fn in COMBINES:
                # Join key/column lists are scalar metadata, not data sources.
                if fn in {'Table.Join', 'Table.NestedJoin'} and len(args) >= 3:
                    result = union([args[0], args[2]])
                    result.effects.append('Merge/join; inputs may affect row counts or filtering without supplying displayed columns')
                    return result
                result = union(args)
                result.effects.append('Combined inputs; source-column contribution not proven')
                return result
            if fn in PURE or (pure_library(fn) and fn not in self.definitions):
                return union(args)
            function = resolve(fn)
            if fn in self.definitions and function.kind == 'function':
                # Invoking one of the model's own functions (Combine files'
                # Transform File): sources come from its arguments and its body.
                return union(args + [function])
            if function.references or function.connections or function.objects:
                args.append(function)
            namespace = fn.split('.', 1)[0] if '.' in fn else ''
            if (namespace and namespace[0].isupper() and namespace not in LIBRARY and fn not in self.definitions
                    and args and args[0].kind == 'text'):
                # An unrecognised connector: keep its first argument as the identity.
                conn = dict(sourceType=fn + ' (unrecognised connector)', server=args[0].text, database='', schema='',
                            primaryQuery=self.current_query)
                result = union(args[1:], 'Connector not recognised; identity taken from its first argument: ' + fn)
                result.connections, result.objects, result.kind = [conn], [], 'connection'
                return result
            return union(args, 'Unsupported M function; dependencies may be incomplete: ' + fn)
        # Do not execute branches/lambdas. Retain known references as possible
        # sources and flag incompleteness, rather than choosing a branch.
        values = []
        for t in ts:
            if t.kind == 'id' and t.value not in {'each', 'if', 'then', 'else', 'true', 'false', 'null'}:
                candidate = resolve(t.value)
                if candidate.connections or candidate.objects:
                    values.append(candidate)
        if ts[0].kind == 'id' and ts[0].value == 'each':
            # A row function (each ...) computes values per row; any query it
            # reads is kept above, so it is not a coverage gap by itself.
            return union(values)
        return union(values, 'Unsupported/dynamic M expression; source coverage is incomplete')

    def internal(self, fn, arg_tokens, resolve):
        """Tables built inside Power Query. Arguments may still reference other
        queries (a calendar spanning a sales table); those stay as sources."""
        args = [self.evaluate(arg, resolve) for arg in arg_tokens if arg]
        if fn in FROM_LIST and args and args[0].kind == 'generated':
            args[0].kind = 'table'
        inputs = union(args)
        if inputs.connections or inputs.objects:
            inputs.kind = 'table'
            return inputs
        if fn in GENERATORS:
            inputs.kind = 'generated'
            return inputs
        generated = fn in FROM_LIST and arg_tokens and not (arg_tokens[0] and arg_tokens[0][0].value == '{')
        kind = 'Generated in Power Query' if generated else 'Entered data'
        inputs.objects = [dict(sourceType=kind, server='', database='', schema='', object='', sql='',
                               evidence=f'{fn} builds the table inside Power Query', notes=[])]
        inputs.kind = 'table'
        return inputs

    def external_filter(self, ts, resolve):
        # Only exact conjunctions of Name/Folder Path equalities are refined.
        # OR, functions, index access and dynamic predicates retain the collection.
        if not ts or ts[0].value != 'each':
            return None
        fields = {}
        for part in split(ts[1:], 'and'):
            pairs = pairs_for(part)
            end = pairs.get(0)
            if end is None or part[0].value != '[' or end+1 >= len(part) or part[end+1].value != '=':
                return None
            key = ' '.join(t.value for t in part[1:end]).lower()
            value = self.evaluate(part[end+2:], resolve)
            if key not in {'name', 'folder path'} or value.kind != 'text' or key in fields:
                return None
            fields[key] = value.text
        return fields if 'name' in fields else None

    def connector(self, fn, args):
        result = union(args)
        result.objects, result.connections = [], []
        kind = CONNECTORS[fn]
        server = args[0].text if args and args[0].kind == 'text' else ''
        database = args[1].text if fn in {'Sql.Database', 'AnalysisServices.Database'} and len(args) > 1 and args[1].kind == 'text' else ''
        conn = dict(sourceType=kind, server=server or '', database=database or '', schema='', primaryQuery=self.current_query)
        if kind == 'Analysis Services':
            from .live_connection import analysis_services_kind
            conn['sourceType'] = analysis_services_kind(server)
        if not server:
            result.issues.append('Connection/server expression is unresolved')
        if fn.startswith('Odbc.'):
            parts = {}
            # Retain only source identity fields, never credentials.
            for match in re.finditer(r'(?:^|;)\s*([^=;]+)=\s*(\{(?:[^}]|}})*\}|[^;]*)', server or ''):
                parts[match[1].strip().lower()] = match[2].strip().strip('{}')
            driver = parts.get('driver', '').lower()
            conn['sourceType'] = 'Oracle' if 'oracle' in driver else 'Teradata' if 'teradata' in driver else 'SQL Server' if 'sql server' in driver else 'ODBC'
            conn['server'] = parts.get('server') or parts.get('dbq') or parts.get('data source') or ('dsn=' + parts['dsn'] if parts.get('dsn') else '')
            conn['database'] = parts.get('database') or parts.get('initial catalog') or ''
            if parts.get('dsn'):
                result.issues.append('ODBC DSN requires external configuration to resolve its physical connection')
            if not conn['server']:
                result.issues.append('ODBC connection identity is unresolved')
        if kind == 'Oracle' and server and '/' in server and not server.startswith('('):
            conn['database'] = server.rsplit('/', 1)[1]
        result.connections = [conn]
        result.kind = 'connection'
        opts_index = 2 if fn in {'Sql.Database', 'AnalysisServices.Database'} else 1
        if fn == 'Odbc.Query':
            return self.sql(result, args[1] if len(args) > 1 else Value())
        if len(args) > opts_index and 'Query' in args[opts_index].members:
            return self.sql(result, args[opts_index].members['Query'])
        return result

    def sql(self, target, query):
        result = union([target, query])
        result.objects = []
        if query.kind != 'text':
            result.issues.append('Native SQL text is dynamic or unavailable')
            for conn in target.connections or [dict(sourceType='Unknown', server='', database='', schema='')]:
                result.objects.append(dict(conn, object='', sql='', evidence='Unresolved native SQL', notes=['Native SQL text is dynamic or unavailable']))
            return result
        connections = list({tuple(sorted((k, v) for k, v in c.items() if k != 'primaryQuery')): c for c in target.connections}.values())
        if len(connections) != 1:
            result.issues.append('Native SQL target connection is ambiguous or unresolved')
            conn = dict(sourceType='Unknown', server='', database='', schema='')
        else:
            conn = connections[0]
        objects, issues = extract_sql_objects(query.text)
        result.issues += issues
        for parts in objects:
            row = dict(conn, object=parts[-1], sql=query.text, evidence='Native SQL object: ' + '.'.join(parts), notes=[])
            if len(parts) == 2:
                row['database' if conn['sourceType'] == 'Teradata' else 'schema'] = parts[0]
            elif len(parts) == 3:
                row['database'], row['schema'] = parts[:2]
            elif len(parts) == 4 and conn['sourceType'] == 'SQL Server':
                row['server'], row['database'], row['schema'] = parts[:3]
                row['notes'].append('Four-part SQL server name may be a linked-server alias')
            elif len(parts) > 3:
                row['object'] = '.'.join(parts)
                row['notes'].append('Qualified object naming is not supported for this dialect')
            if '@' in row['object']:
                row['notes'].append('Object uses a database link; server is the entry connection, not a verified remote host')
            if len(parts) <= 2 and conn['sourceType'] != 'Teradata' and any('database context with USE' in issue for issue in issues):
                row['database'] = ''
                row['notes'].append('SQL USE overrides the connection database; inspect the original SQL')
            if len(parts) == 1:
                row['notes'].append('Unqualified SQL object; default schema/database not verified')
            result.objects.append(row)
        if not objects:
            result.objects.append(dict(conn, object='', sql=query.text, evidence='Native SQL', notes=['No physical source object resolved from this SQL']))
        elif issues:
            # Keep a distinct coverage row so known objects do not hide an
            # unsupported source elsewhere in the same statement.
            result.objects.append(dict(conn, object='', sql=query.text, evidence='SQL coverage gap', notes=issues))
        if len(connections) == 1:
            for row in result.objects:
                row['primaryQueries'] = sorted({c.get('primaryQuery', '') for c in target.connections} - {''})
        result.kind = 'table'
        return result

    def navigate(self, base, record):
        result = union([base, record])
        if (record.kind == 'literal' and record.text.isdigit() and base.connections
                and all(c.get('navigationMode') in {'files', 'hierarchy'} for c in base.connections)):
            # Folder{0}: a sample file for Combine files. The source stays the folder.
            return result
        if record.kind != 'record' or not base.connections:
            result.issues.append('Navigation target or key is unresolved')
            return result
        if base.connections and all(c.get('navigationMode') in {'file', 'document', 'endpoint'} for c in base.connections):
            return result
        fields = {k.lower(): v.text for k, v in record.members.items() if v.kind == 'text'}
        if len(fields) != len(record.members):
            result.issues.append('Navigation uses a nonliteral key')
            result.objects = [dict(c, object='', sql='', evidence='Unresolved navigation', notes=['Navigation uses a nonliteral key']) for c in base.connections]
            return result
        if len(base.connections) == 1 and base.connections[0].get('navigationMode') == 'dataflow':
            conn = dict(base.connections[0])
            conn['server'] = fields.get('workspaceid') or conn['server']
            conn['database'] = fields.get('dataflowid') or conn['database']
            result.connections, result.objects = [conn], []
            if fields.get('entity'):
                result.objects.append(dict(conn, object=fields['entity'], sql='', evidence='Dataflow navigation', notes=[]))
                result.kind = 'table'
            else:
                result.kind = 'connection'
            return result
        external = navigate_external(result, fields)
        if external is not None:
            return external
        kind = (fields.get('kind') or '').lower()
        name = fields.get('item') or fields.get('name')
        result.connections, result.objects = [], []
        if len(base.connections) != 1:
            result.issues.append('Navigation has multiple possible connections')
            return result
        conn = dict(base.connections[0])
        if kind == 'database' or (conn['sourceType'] in {'SQL Server', 'Teradata'} and not conn['database'] and name and not fields.get('schema') and kind not in {'table', 'view'}):
            conn['database'] = name or ''
        elif kind == 'schema' or (fields.get('schema') and not name):
            namespace = fields.get('schema') or name or ''
            conn['database' if conn['sourceType'] == 'Teradata' else 'schema'] = namespace
        elif name:
            schema = fields.get('schema') or conn['schema']
            if conn['sourceType'] == 'Teradata':
                conn['database'] = schema or conn['database']
                schema = ''
            row = dict(conn, schema=schema, object=name, sql='', evidence='M navigation: ' + repr(fields), notes=[])
            if not schema and conn['sourceType'] in {'SQL Server', 'Oracle'}:
                row['notes'].append('Navigation schema is not supplied')
            result.objects.append(row)
        else:
            result.issues.append('Navigation has no identifiable object name')
        result.connections = [conn]
        result.kind = 'table' if result.objects else 'connection'
        return result


COLLECTION_UNRESOLVED = 'Collection location identified; individual item selection unresolved'


def combine_folder(value):
    """Combine files: a function applied to every file of a folder listing
    (Folder.Files, SharePoint.Files). The source is the folder as a whole; no
    individual file is claimed."""
    for row in value.objects:
        if row.get('sourceKind') in {'files', 'hierarchy'} and not row.get('object') and row.get('location'):
            row['object'] = '(all files)'
            row['notes'] = [n for n in row.get('notes', []) if n != COLLECTION_UNRESOLVED]
            row['evidence'] = 'Combine files: every file in the folder is read'
            row['objectType'] = 'Folder (combined files)'


def materialize(value):
    rows = copy.deepcopy(value.objects)
    if rows and not any(not r.get('object') for r in rows) and any('Unsupported' in issue or 'Cyclic' in issue for issue in value.issues):
        rows.append(dict(sourceType='Unknown', server='', database='', schema='', object='', sql='', evidence='M coverage gap', notes=value.issues[:]))
    if not rows:
        for conn in value.connections or [dict(sourceType='Unknown', server='', database='', schema='')]:
            rows.append(dict(conn, object='', sql='', evidence='M expression', notes=['Source object unresolved']))
    internal = [r for r in rows if r.get('sourceType') in INTERNAL_SOURCES]
    if internal and len(internal) < len(rows):
        rows = [r for r in rows if r.get('sourceType') not in INTERNAL_SOURCES]
    for row in rows:
        if row.get('sourceType') in INTERNAL_SOURCES:
            row.update(notes=[], status='Not applicable', preparationEffects=sorted(set(value.effects)),
                       primaryQueries=row.get('primaryQueries') or [], referencedQueries=sorted(value.references),
                       referencedM='\n\n'.join(f'// Referenced query: {n}\n{c}' for n, c in sorted(value.references.items())))
            continue
        notes = set(row.get('notes', [])) | set(value.issues)
        if not row.get('server'):
            notes.add('Server/connection unresolved')
        tns_alias = (row.get('sourceType') == 'Oracle' and row.get('server')
                     and not any(c in row['server'] for c in '/:('))
        # An Oracle TNS alias names the service itself; Teradata has no database
        # in its connection (it is the navigation schema or the SQL qualifier).
        if not row.get('database') and not row.get('navigationMode') and not tns_alias \
                and not (row.get('sourceType') == 'Teradata' and row.get('object')):
            notes.add('Database/service not supplied or unresolved')
        if row.get('sourceType') == 'Oracle' and row.get('sql'):
            # Oracle folds unquoted identifiers to upper case: billing.tariff_plan
            # and BILLING.TARIFF_PLAN are one object.
            for key in ('schema', 'object'):
                name = row.get(key) or ''
                if name and f'"{name}"' not in row['sql']:
                    row[key] = name.upper()
        row['notes'] = sorted(notes)
        row['status'] = 'Unresolved' if not row['object'] else 'Partial' if notes else 'Resolved'
        row['preparationEffects'] = sorted(set(value.effects))
        row['primaryQueries'] = row.get('primaryQueries') or ([row['primaryQuery']] if row.get('primaryQuery') else [])
        row['referencedQueries'] = sorted(value.references)
        row['referencedM'] = '\n\n'.join(f'// Referenced query: {name}\n{code}' for name, code in sorted(value.references.items()))
    return rows
