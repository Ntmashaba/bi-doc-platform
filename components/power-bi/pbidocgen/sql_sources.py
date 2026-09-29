"""Conservative, offline source-object extraction for SELECT SQL.

This is a structural scanner, not a SQL execution engine or full SQL grammar.
Unsupported sources retain diagnostics instead of inventing object identities.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Token:
    text: str
    kind: str = 'word'


def tokens(sql):
    out, issues, i = [], [], 0
    while i < len(sql):
        c = sql[i]
        if c.isspace():
            i += 1
            continue
        if sql.startswith('--', i):
            end = sql.find('\n', i)
            i = len(sql) if end < 0 else end
            continue
        if sql.startswith('/*', i):
            depth, i = 1, i + 2
            while i < len(sql) and depth:
                if sql.startswith('/*', i):
                    depth, i = depth + 1, i + 2
                elif sql.startswith('*/', i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            if depth:
                issues.append('Unterminated SQL comment')
            continue
        # Oracle alternative quoted strings can contain ordinary apostrophes.
        if c.lower() == 'q' and sql[i:i + 2].lower() == "q'" and i + 2 < len(sql):
            opener = sql[i + 2]
            closer = {'[': ']', '{': '}', '(': ')', '<': '>'}.get(opener, opener)
            end = sql.find(closer + "'", i + 3)
            if end < 0:
                issues.append('Unterminated Oracle SQL literal')
                break
            out.append(Token('', 'string'))
            i = end + 2
            continue
        if c in "'\"[`":
            close = ']' if c == '[' else c
            kind = 'string' if c == "'" else 'identifier'
            value, i, closed = '', i + 1, False
            while i < len(sql):
                if sql[i] == close:
                    if i + 1 < len(sql) and sql[i + 1] == close:
                        value, i = value + close, i + 2
                        continue
                    i, closed = i + 1, True
                    break
                value, i = value + sql[i], i + 1
            if not closed:
                issues.append('Unterminated SQL literal or identifier')
            out.append(Token(value if kind != 'string' else '', kind))
            continue
        if c.isalpha() or c in '_#$':
            end = i + 1
            while end < len(sql) and (sql[end].isalnum() or sql[end] in '_#$'):
                end += 1
            out.append(Token(sql[i:end]))
            i = end
            continue
        out.append(Token(c, 'symbol'))
        i += 1
    return out, issues


def extract_sql_objects(sql: str):
    ts, issues = tokens(sql)
    pairs, stack = {}, []
    for i, token in enumerate(ts):
        if token.text == '(' and token.kind == 'symbol':
            stack.append(i)
        elif token.text == ')' and token.kind == 'symbol':
            if not stack:
                issues.append('Unbalanced SQL parentheses')
            else:
                start = stack.pop()
                pairs[start] = i
    if stack:
        issues.append('Unbalanced SQL parentheses')
    objects = []
    def word(i, text):
        return i < len(ts) and ts[i].kind == 'word' and ts[i].text.upper() == text
    def ident(i):
        return i < len(ts) and ts[i].kind in ('word', 'identifier')
    stops = {'WHERE', 'GROUP', 'ORDER', 'HAVING', 'QUALIFY', 'UNION', 'EXCEPT', 'INTERSECT', 'MINUS', 'LIMIT', 'OFFSET', 'FETCH', 'RETURNING'}
    reserved = stops | {'SELECT', 'ON', 'JOIN', 'LEFT', 'RIGHT', 'FULL', 'CROSS', 'INNER', 'OUTER', 'AS', 'WITH', 'INTO'}
    def scan(start, end, outer_ctes=frozenset()):
        visible = set(outer_ctes)
        i = start
        while i < end and ts[i].text == ';':
            i += 1
        if word(i, 'WITH'):
            i += 1
            if word(i, 'RECURSIVE'):
                i += 1
            while i < end and ident(i):
                name, i = ts[i].text.casefold(), i + 1
                if i in pairs:  # CTE column list
                    i = pairs[i] + 1
                if not word(i, 'AS'):
                    issues.append('Unrecognised SQL WITH clause')
                    break
                i += 1
                if i not in pairs:
                    issues.append('Unrecognised CTE definition')
                    break
                visible.add(name)
                scan(i + 1, pairs[i], visible)
                i = pairs[i] + 1
                if i >= end or ts[i].text != ',':
                    break
                i += 1
        in_from, expect_source, saw_select = False, False, False
        while i < end:
            t = ts[i]
            upper = t.text.upper() if t.kind == 'word' else ''
            if upper == 'SELECT':
                saw_select = True
            if upper == 'USE':
                issues.append('SQL changes database context with USE; default database is unresolved')
            if upper in {'EXEC', 'EXECUTE', 'CALL', 'INSERT', 'UPDATE', 'DELETE', 'MERGE', 'CREATE', 'ALTER', 'DROP'}:
                issues.append('Non-SELECT/dynamic SQL requires review')
            if t.text == ';' and t.kind == 'symbol':
                # CTE scope ends at the statement, not at the whole SQL string.
                if i + 1 < end:
                    scan(i + 1, end, outer_ctes)
                return
            if upper in stops:
                in_from, expect_source = False, False
            if upper in {'FROM', 'JOIN', 'APPLY'}:
                in_from, expect_source = True, True
                i += 1
                continue
            if in_from and t.text == ',' and t.kind == 'symbol':
                expect_source = True
                i += 1
                continue
            if i in pairs:
                close = pairs[i]
                # Only query-containing parentheses are query scopes. EXTRACT's
                # FROM and string literals are not table references.
                if any(x.kind == 'word' and x.text.upper() in {'SELECT', 'WITH'} for x in ts[i + 1:close]):
                    scan(i + 1, close, visible)
                elif expect_source:
                    issues.append('Unrecognised parenthesised SQL source')
                expect_source = False
                i = close + 1
                continue
            if expect_source:
                if upper in {'LATERAL', 'ONLY'}:
                    i += 1
                    continue
                if not ident(i) or upper in reserved:
                    issues.append('Unresolved SQL source expression')
                    expect_source = False
                    i += 1
                    continue
                parts, i = [t.text], i + 1
                while i < end and ts[i].text == '.':
                    i += 1
                    if i < end and ts[i].text == '.':
                        parts.append('')
                        continue
                    if i < end and ident(i):
                        parts.append(ts[i].text)
                        i += 1
                    else:
                        issues.append('Incomplete qualified SQL object')
                        break
                if i in pairs:
                    issues.append('Table-valued function or external SQL source: ' + '.'.join(parts))
                    i = pairs[i] + 1
                elif i < end and ts[i].text == '@':
                    issues.append('Oracle database link requires remote connection metadata')
                    i += 1
                    link = ts[i].text if ident(i) else '?'
                    parts[-1] += '@' + link
                    objects.append(parts)
                    i += 1
                elif len(parts) > 1 or parts[0].casefold() not in visible:
                    if parts[0].startswith('#'):
                        issues.append('Temporary SQL object: ' + parts[0])
                    else:
                        objects.append(parts)
                expect_source = False
                continue
            i += 1
        if not saw_select and start < end:
            issues.append('SQL statement is not a recognised SELECT')
    scan(0, len(ts))
    if issues and any('Unterminated' in x or 'Unbalanced' in x for x in issues):
        return [], sorted(set(issues))
    # Preserve quoted spelling/case rather than assuming database collation.
    return list({tuple(p): p for p in objects}.values()), sorted(set(issues))
