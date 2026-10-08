"""Applied Steps read from M as written: structure first, words only where the form is recognised."""
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pbidocgen import m_steps  # noqa: E402
from pbidocgen.m_sources import tokenize  # noqa: E402


def status(code, queries=None):
    reading = m_steps.read(code, queries)
    return reading.extraction["status"], reading.status, reading.kind, reading.names


def steps(code, queries=None):
    reading = m_steps.read(code, queries)
    assert reading.status == "parsed", (reading.status, reading.note)
    return reading


def says(expression, previous="Source", queries=None):
    """The description of one step written after a step named `previous`."""
    reading = steps(f'let\n    {previous} = 1,\n    Step = {expression}\nin\n    Step', queries)
    return reading.steps[1].description


LONG = '''let
    // where the data comes from
    Source = Sql.Database("srv", "dw", [CommandTimeout = #duration(0, 0, 10, 0)]),
    Orders = Source{[Schema = "dbo", Item = "Orders"]}[Data],   // the navigation step
    #"Changed Type" = Table.TransformColumnTypes(Orders, {{"Amount", Currency.Type}, {"Placed", type date},
        {"Note, with comma", type text}}),
    /* a nested let is part of its step,
       not a step of the query */
    #"Added ""Net"", say" = Table.AddColumn(#"Changed Type", "Net", each
        let
            Gross = [Amount],
            Tax = if Gross > 100 then Gross * 0.2 else 0
        in
            Gross - Tax, type number),
    #"in" = Table.SelectRows(#"Added ""Net"", say", each [Note] <> "let x = 1, y = 2 in x" and [Net] > 0),
    Última = Table.Distinct(#"in")
in
    Última'''


class Structure(unittest.TestCase):
    def test_steps_come_in_source_order_under_the_names_the_author_gave(self):
        reading = steps(LONG)
        self.assertEqual(reading.names, ["Source", "Orders", "Changed Type", 'Added "Net", say', "in", "Última"])
        self.assertEqual((reading.extraction["status"], reading.kind, reading.scope, reading.returns), ("complete", "query", "query", ""))

    def test_each_step_keeps_its_expression_exactly_as_written(self):
        reading = steps(LONG)
        by = {s.name: LONG[s.start:s.end] for s in reading.steps}
        self.assertEqual(by["Source"], 'Sql.Database("srv", "dw", [CommandTimeout = #duration(0, 0, 10, 0)])')
        self.assertEqual(by["Orders"], 'Source{[Schema = "dbo", Item = "Orders"]}[Data]')
        self.assertTrue(by['Added "Net", say'].startswith('Table.AddColumn(#"Changed Type", "Net", each\n        let\n'))
        self.assertTrue(by['Added "Net", say'].endswith("Gross - Tax, type number)"))
        self.assertEqual(by["Última"], 'Table.Distinct(#"in")')
        # Together the steps cover the let: nothing of one step is shown under another.
        spans = [(s.start, s.end) for s in reading.steps]
        self.assertEqual(spans, sorted(spans))
        self.assertTrue(all(a_end <= b_start for (_, a_end), (b_start, _) in zip(spans, spans[1:])))

    def test_comments_belong_to_the_step_they_are_written_for(self):
        reading = steps(LONG)
        notes = {s.name: s.comment for s in reading.steps}
        self.assertEqual(notes["Source"], "where the data comes from")
        self.assertEqual(notes["Orders"], "the navigation step")                 # on the step's own line
        self.assertEqual(notes['Added "Net", say'], "a nested let is part of its step, not a step of the query")
        self.assertEqual(notes["Changed Type"], "")
        last = steps("let\n  A = 1, // about A\n  // about B\n  B = 2 // also B\nin B")
        self.assertEqual([s.comment for s in last.steps], ["about A", "about B also B"])

    def test_text_that_looks_like_structure_is_not_structure(self):
        cases = {
            'let A = "a, b", B = "let x in y", C = #"in A, B" & A, #"in A, B" = "]})" in C': ["A", "B", "C", "in A, B"],
            'let A = [x = 1, y = [z = {1, 2, 3}]], B = {[a = 1], [a = 2]} in B': ["A", "B"],
            'let A = try Number.From("x") otherwise null, B = if A = null then 0 else A in B': ["A", "B"],
            'let A = type table [Name = text, Age = number], B = #table(A, {}) in B': ["A", "B"],
            'let A = (x, y) => let s = x + y, d = s * 2 in d, B = A(1, 2) in B': ["A", "B"],
            'let A = each let k = _ + 1, j = k * 2 in j, B = List.Transform({1, 2}, A) in B': ["A", "B"],
            'let A = 1 /* B = 2, */ , C = 3 // D = 4,\n in C': ["A", "C"],
            'let IsBig = Amount = Limit, Other = IsBig = true in Other': ["IsBig", "Other"],     # = is also "equals"
            'let A = 1e5, B = 0xFF, C = A + B in C': ["A", "B", "C"],
            'let\r\n    A = 1,\r\n    B = A + 1\r\nin\r\n    B': ["A", "B"],
            'let #"let" = 1, #"each" = #"let" + 1 in #"each"': ["let", "each"],
            'let A = 1 in A meta [Documentation.Name = "x, y"]': ["A"],
            '(let A = 1, B = A in B)': ["A", "B"],
        }
        for code, expected in cases.items():
            self.assertEqual(status(code)[1:], ("parsed", "query", expected), code)

    def test_a_nested_let_never_adds_steps_to_the_query(self):
        code = "let Outer = let Inner1 = 1, Inner2 = let Deep = 2 in Deep in Inner1 + Inner2, Last = Outer in Last"
        self.assertEqual(status(code)[3], ["Outer", "Last"])

    def test_a_let_that_returns_an_earlier_step_says_so(self):
        self.assertEqual(steps("let A = 1, B = A + 1, C = B + 1 in B").returns, "B")
        self.assertEqual(steps("let A = 1, B = A + 1 in B").returns, "")
        self.assertEqual(steps("let A = 1, B = A + 1 in A + B").returns, "")         # an expression, not a step

    def test_an_expression_without_let_has_no_steps_and_that_is_not_a_failure(self):
        for code in ('"West"', "42", "#date(2024, 1, 1)", 'Sql.Database("s", "d")', "Stage", "{1, 2, 3}", "[A = 1, B = 2]",
                     'if Flag then "a" else "b"', "// only a note\n1", "Table.Combine({A, B})", 'A & "x"', "#table({}, {})"):
            self.assertEqual(status(code)[:2] + (status(code)[3],), ("complete", "none", []), code)

    def test_a_parameter_is_a_parameter_whatever_its_value(self):
        self.assertEqual(status('"West" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]')[1:3], ("none", "parameter"))
        self.assertEqual(status('#datetime(2024, 1, 1, 0, 0, 0) meta [IsParameterQuery = true, Type = "DateTime"]')[2], "parameter")
        self.assertEqual(status('"IsParameterQuery=true"')[2], "query")

    def test_a_function_shows_the_steps_of_its_body(self):
        code = "(t as table, optional n as number) as table =>\nlet\n    A = Table.FirstN(t, n),\n    B = Table.Distinct(A)\nin\n    B"
        reading = steps(code)
        self.assertEqual((reading.kind, reading.scope, reading.names), ("function", "function body", ["A", "B"]))
        self.assertEqual(status("(x) => x + 1"), ("complete", "none", "function", []))
        self.assertEqual(status("let\n    Source = (x as number) => x * 2\nin\n    Source")[1:3], ("parsed", "function"))
        self.assertEqual(status("let Source = (1 + 2) * 3 in Source")[2], "query")
        self.assertEqual(status("(x) => (y) => let A = x + y in A")[1:], ("none", "function", []))   # the body is a function, not a let

    def test_text_that_stops_inside_a_construct_is_known_partial(self):
        for code in ('let\n    Source = Sql.Database("server", "db', "let Source = Table.FromRows({{1, 2}", "let A = 1 /* note",
                     'let A = #"Changed', "let A = (1 + 2"):
            self.assertEqual(status(code)[:2] + (status(code)[3],), ("known partial", "unsupported", []), code)
        self.assertIn("may hold only part", m_steps.read('let A = "x').extraction["note"])

    def test_complete_text_that_is_not_understood_is_unsupported_not_partial(self):
        cases = {"let A = 1, A = 2 in A": "share the name A", "let A = 1 B = 2 in B": "comma is missing before the step B",
                 "let A = 1, in A": "empty", "let A = 1": "no matching in", "let A 1 in A": "name = expression",
                 "let A = in A": "no expression", "let A = 1 in": "Nothing follows in",
                 "let Source = Csv.Document(x) #\"Promoted\" = Table.PromoteHeaders(Source) in #\"Promoted\"": "comma is missing",
                 "section Section1; shared A = 1;": "section document", 'let A = "#(zz)" in A': "could not be read"}
        for code, reason in cases.items():
            reading = m_steps.read(code)
            self.assertEqual((reading.extraction["status"], reading.status, reading.names), ("complete", "unsupported", []), code)
            self.assertIn(reason, reading.note, code)

    def test_no_expression_is_unavailable(self):
        for code in (None, "", "  \n "):
            self.assertEqual(status(code)[:2], ("unavailable", "none"))

    def test_no_prefix_of_a_script_makes_the_reader_fail(self):
        """A file can be cut anywhere. Every prefix gives one of the statuses, never an exception."""
        scripts = [LONG, "(t as table) as table =>\nlet\n    A = Table.FirstN(t, 5)\nin\n    A",
                   'let S = Source{[Name="x"]}[Data], T = Table.Combine({S, S}) in T meta [a = 1]']
        for script in scripts:
            for cut in range(len(script) + 1):
                reading = m_steps.read(script[:cut])
                self.assertIn(reading.status, m_steps.STEPS)
                self.assertIn(reading.extraction["status"], m_steps.EXTRACTION)
        rng = random.Random(7)
        alphabet = list('let in each = , ( ) [ ] { } " # / * \n A B "x" 1 => meta')
        for _ in range(3000):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 40)))
            reading = m_steps.read(text)
            self.assertIn(reading.status, m_steps.STEPS, text)

    def test_positions_are_given_the_way_a_browser_counts(self):
        code = 'let A = "😀 two units", B = Text.Length(A) in B'
        reading = steps(code)
        at = m_steps.utf16_offsets(code)
        units = code.encode("utf-16-le")
        for step in reading.steps:
            self.assertEqual(units[2 * at(step.start):2 * at(step.end)].decode("utf-16-le"), code[step.start:step.end])
        self.assertEqual(at(len(code)), len(units) // 2)
        plain = "let A = 1 in A"
        self.assertEqual(m_steps.utf16_offsets(plain)(7), 7)

    def test_tokens_know_where_they_are_and_whether_they_were_quoted(self):
        code = 'let #"A b" = "x", C = #"A b" in C'
        for token in tokenize(code):
            written = code[token.start:token.end]
            self.assertEqual(written, f'#"{token.value}"' if token.quoted else f'"{token.value}"' if token.kind == "string" else token.value)

    def test_references_respect_steps_fields_and_parameters_of_the_same_name(self):
        names = {"Stage", "Region", "Sales", "fnClean", "t", "in"}
        refs = lambda code: m_steps.references(m_steps.read(code).tokens, names)  # noqa: E731
        self.assertEqual(refs('let Source = Stage, K = Table.SelectRows(Source, each [Region] = Region) in fnClean(K)'),
                         ["Stage", "Region", "fnClean"])
        self.assertEqual(refs('let Stage = 1, X = Stage + 1 in X'), [])                   # a step of that name hides the query
        self.assertEqual(refs('let X = Table.SelectRows(T, each [Sales] > 0 and _[Region] = "W") in X'), [])    # fields
        self.assertEqual(refs('let X = [Sales = 1, Region = 2] in X'), [])                 # record fields being set
        self.assertEqual(refs('(t as table) => Table.Join(t, "k", Sales, "k")'), ["Sales"])  # t is the parameter
        self.assertEqual(refs('let X = "Stage" in X'), [])                                  # text is not a reference
        self.assertEqual(refs('let X = #"Stage" in X'), ["Stage"])
        self.assertEqual(refs('let X = #"in" in X'), ["in"])                                # a query really named "in"
        self.assertEqual(refs('let X = 1 in X'), [])                                        # the keyword is not that query
        # a name is hidden only where it is defined: a record field, a parameter or a step elsewhere in the
        # expression leaves the query of that name a reference everywhere else
        self.assertEqual(refs('let Source = Stage, Added = Table.AddColumn(Source, "Details", each [Stage = 1]) in Added'), ["Stage"])
        self.assertEqual(refs('let a = [Stage = 1], b = Stage in b'), ["Stage"])
        self.assertEqual(refs('[Stage = 1, b = Stage][b]'), [])                             # fields see one another
        self.assertEqual(refs('let f = (Stage) => Stage + 1, x = f(Stage) in x'), ["Stage"])
        self.assertEqual(refs('let f = (t as table) => Table.Join(t, "k", Stage, "k") in f(Sales)'), ["Stage", "Sales"])
        self.assertEqual(refs('let a = let Stage = 2 in Stage, b = Stage in b'), ["Stage"])  # a nested let's step
        self.assertEqual(refs('let a = Stage, Stage = 1 in a'), [])                         # a later step, still that let's
        # a scope ends with its expression: at the enclosing let's `in`, an if's `then` or `else`, a try's `otherwise`,
        # including when the function or nested let is the last binding, with no comma after it
        self.assertEqual(refs('let Transform = (Stage) => Stage in Transform(Stage)'), ["Stage"])
        self.assertEqual(refs('let a = let Stage = 1 in Stage in Stage'), ["Stage"])
        self.assertEqual(refs('if Sales then (Stage) => Stage else Stage'), ["Sales", "Stage"])
        self.assertEqual(refs('if Sales then let Stage = 1 in Stage else Stage'), ["Sales", "Stage"])
        self.assertEqual(refs('try (Stage) => Stage otherwise Stage'), ["Stage"])
        self.assertEqual(refs('try let Stage = 1 in Stage otherwise Stage'), ["Stage"])
        self.assertEqual(refs('let f = (x) => if x then let Stage = 1 in Stage else Stage in f(1)'), ["Stage"])
        self.assertEqual(refs('let f = (Stage) => try Stage, g = Stage in g'), ["Stage"])   # a try with no otherwise
        # ...and the body keeps its own keywords: the parameter still hides the query throughout it
        self.assertEqual(refs('let f = (Stage) => if Stage then Stage else Stage in f(Sales)'), ["Sales"])
        self.assertEqual(refs('let f = (Stage) => try Stage otherwise Stage in f(Sales)'), ["Sales"])
        self.assertEqual(refs('let f = (Stage) => let x = Stage, y = Stage in y in f(Sales)'), ["Sales"])


class Descriptions(unittest.TestCase):
    """A step is put into words only when its operation and its arguments are both recognised."""

    def test_steps_the_editor_writes(self):
        cases = {
            'Table.PromoteHeaders(Source, [PromoteAllScalars=true])': "Uses the first row as column headers",
            'Table.TransformColumnTypes(Source,{{"Amount", Currency.Type}, {"Placed", type date}, {"Id", Int64.Type}})':
                "Sets the data type of 3 columns: Amount (fixed decimal number), Placed (date), Id (whole number)",
            'Table.TransformColumnTypes(Source,{{"A", type text}}, "en-GB")': "Sets the data type of 1 column: A (text)",
            'Table.RemoveColumns(Source,{"A", "B"})': "Removes 2 columns: A, B",
            'Table.RemoveColumns(Source,"A")': "Removes 1 column: A",
            'Table.SelectColumns(Source,{"A"})': "Keeps only 1 column: A",
            'Table.RenameColumns(Source,{{"Old", "New"}, {"a", "b"}})': "Renames 2 columns: Old → New, a → b",
            'Table.RenameColumns(Source,{"Old", "New"})': "Renames 1 column: Old → New",
            'Table.SelectRows(Source, each [Region] = "West" and [Amount] > 0)': 'Keeps rows where [Region] = "West" and [Amount] > 0',
            'Table.Distinct(Source)': "Removes duplicate rows",
            'Table.Distinct(Source, {"Id"})': "Removes duplicate rows, comparing Id",
            'Table.Sort(Source,{{"Placed", Order.Descending}, {"Id", Order.Ascending}})': "Sorts rows by Placed (descending), Id (ascending)",
            'Table.AddColumn(Source, "Net", each [Amount] * 0.8, type number)': "Adds the column Net, as decimal number",
            'Table.AddColumn(Source, "Custom", each 1)': "Adds the column Custom",
            'Table.ReplaceValue(Source,"a","b",Replacer.ReplaceText,{"Name"})': 'Replaces "a" with "b" in Name',
            'Table.ReplaceValue(Source,null,0,Replacer.ReplaceValue,{"Qty", "Amount"})': "Replaces null with 0 in Qty, Amount",
            'Table.Group(Source, {"Region"}, {{"Total", each List.Sum([Amount]), type number}, {"Rows", each Table.RowCount(_), Int64.Type}})':
                "Groups rows by Region; adds Total, Rows",
            'Table.NestedJoin(Source, {"CustomerId"}, Customers, {"Id"}, "Customers", JoinKind.LeftOuter)':
                "Merges with Customers on CustomerId = Id (left outer); the matching rows go into the column Customers",
            'Table.ExpandTableColumn(Source, "Customers", {"Name", "City"}, {"Customer", "City"})':
                "Expands the column Customers into Name (as Customer), City",
            'Table.Combine({Source, Other, Third})': "Appends Source, Other, Third into one table",
            'Table.UnpivotOtherColumns(Source, {"Id"}, "Attribute", "Value")': "Unpivots every column except Id into Attribute and Value",
            'Table.Pivot(Source, List.Distinct(Source[Kind]), "Kind", "Value", List.Sum)': "Pivots the values of Kind into columns, filled from Value",
            'Table.FirstN(Source, 100)': "Keeps the first 100 rows",
            'Table.Skip(Source, 1)': "Removes the first 1 row",
            'Table.FillDown(Source,{"Region"})': "Fills empty cells downwards in Region",
            'Table.SplitColumn(Source, "Name", Splitter.SplitTextByDelimiter(" "), {"First", "Last"})': "Splits the column Name into First, Last",
            'Table.DuplicateColumn(Source, "Name", "Name - Copy")': "Copies the column Name as Name - Copy",
            'Table.TransformColumns(Source,{{"Name", Text.Trim, type text}})': "Transforms 1 column: Name with Text.Trim",
            'Table.AddIndexColumn(Source, "Index", 1, 1, Int64.Type)': "Adds the index column Index, starting at 1",
            'Table.RemoveRowsWithErrors(Source, {"Amount"})': "Removes rows with errors in Amount",
            'Table.Buffer(Source)': "Holds the table in memory while the query runs",
            'Source{[Schema="dbo",Item="Orders"]}[Data]': "Navigates to dbo.Orders",
            'Source{[Item="Sheet1",Kind="Sheet"]}[Data]': "Navigates to the sheet Sheet1",
            'Source{[Name="FinanceDW"]}[Data]': "Navigates to FinanceDW",
            'Source{0}[Content]': "Takes the first item and reads its Content",
            'Source[Amount]': "Takes the field Amount",
            'Source': "Same as the step Source",
        }
        for expression, expected in cases.items():
            self.assertEqual(says(expression), expected, expression)

    def test_sources(self):
        cases = {
            'Sql.Database("srv01", "FinanceDW")': "Connects to SQL Server: server srv01, database FinanceDW",
            'Sql.Database(ServerName, "FinanceDW")': "Connects to SQL Server: server ServerName, database FinanceDW",
            'Sql.Database("srv01", "FinanceDW", [Query="SELECT a FROM dbo.Secret WHERE id = 7"])':
                "Connects to SQL Server: server srv01, database FinanceDW; runs a native query (the statement is in the script)",
            'Snowflake.Databases("acme.snowflakecomputing.com", "WH")': "Connects to Snowflake: account acme.snowflakecomputing.com, warehouse WH",
            'Excel.Workbook(File.Contents("\\\\share\\finance\\Budget.xlsx"), null, true)': "Reads the Excel workbook \\\\share\\finance\\Budget.xlsx",
            'Csv.Document(Web.Contents("https://files.contoso.com/", [RelativePath = "a/b.csv"]), [Delimiter=","])':
                "Reads the delimited text file https://files.contoso.com/a/b.csv",
            'Csv.Document(Web.Contents(BaseUrl, [RelativePath = "b.csv"]))': "Reads the delimited text file BaseUrl, relative path b.csv",
            'Json.Document(Web.Contents("https://api.contoso.com/v1/orders"))': "Reads JSON from https://api.contoso.com/v1/orders",
            'OData.Feed("https://services.odata.org/V4/Northwind/", null, [Implementation="2.0"])':
                "Connects to the OData feed https://services.odata.org/V4/Northwind/",
            'SharePoint.Files("https://contoso.sharepoint.com/sites/fin", [ApiVersion = 15])':
                "Lists the files of the SharePoint site https://contoso.sharepoint.com/sites/fin",
            'Folder.Files("\\\\share\\drop")': "Lists the files in the folder \\\\share\\drop, subfolders included",
            'Table.FromRows(Json.Document(Binary.Decompress(Binary.FromText("i45WMlTSUTI0MlaK1YlWMgKyjE1MlWJjAQ==", BinaryEncoding.Base64), Compression.Deflate)), let _t = ((type nullable text) meta [Serialized.Text = true]) in type table [A = _t])':
                "Holds data typed into Power Query (Enter data), stored compressed in the script",
            '#table({"A"}, {{1}, {2}})': "Builds a table from values written in the script",
            'Value.NativeQuery(Source, "SELECT 1", null, [EnableFolding=true])': "Runs a native query against Source; the statement is in the script",
        }
        for expression, expected in cases.items():
            self.assertEqual(says(expression), expected, expression)
        # A description never repeats a credential: not the user and password of a URL, nor its query string.
        self.assertEqual(says('Json.Document(Web.Contents("https://svc:Secr3t@api.contoso.com/v1/orders?token=abc123&top=5"))'),
                         "Reads JSON from https://api.contoso.com/v1/orders (with a query string)")
        self.assertEqual(says('OData.Feed("https://reader@feeds.contoso.com/odata?sig=xyz")'),
                         "Connects to the OData feed https://feeds.contoso.com/odata (with a query string)")
        self.assertEqual(says('Web.Contents("https://api.contoso.com/v1/orders")'), "Requests https://api.contoso.com/v1/orders")
        self.assertEqual(says('AzureStorage.DataLake("abfss://raw@lake.dfs.core.windows.net/sales")'),
                         "Connects to Azure Data Lake Storage: abfss://raw@lake.dfs.core.windows.net/sales")
        # A connection string or a statement is never repeated in the description.
        odbc = says('Odbc.DataSource("dsn=Finance;uid=svc;pwd=Secr3t", [HierarchicalNavigation=true])')
        self.assertEqual(odbc, "Connects through ODBC; the connection text is in the script")
        self.assertNotIn("Secr3t", odbc)
        self.assertNotIn("Secret", says('Sql.Database("srv01", "FinanceDW", [Query="SELECT a FROM dbo.Secret"])'))

    def test_the_input_is_named_when_it_is_not_the_step_before(self):
        self.assertEqual(says('Table.Distinct(Orders)', previous="Source"), "Removes duplicate rows of Orders")
        self.assertEqual(says('Table.Distinct(Stage)', queries={"Stage": "query"}), "Removes duplicate rows of the query Stage")
        self.assertEqual(says('Stage', queries={"Stage": "query"}), "Starts from the query Stage")
        self.assertEqual(says('fnClean(Source)', queries={"fnClean": "function"}), "Invokes the function fnClean")
        self.assertEqual(says('Other{[Name="T"]}[Data]', queries={"Other": "query"}), "Navigates to T in the query Other")
        self.assertEqual(says('Table.NestedJoin(Source, {"k"}, Stage, {"k"}, "Stage", JoinKind.Inner)', queries={"Stage": "query"}),
                         "Merges with the query Stage on k = k (inner); the matching rows go into the column Stage")

    def test_a_form_that_is_not_recognised_is_left_without_words(self):
        for expression in ('Table.RemoveColumns(Source, ColumnsToDrop)',                 # which columns is decided elsewhere
                           'Table.RemoveColumns(Source, List.Select(Table.ColumnNames(Source), each Text.StartsWith(_, "x")))',
                           'Table.SelectRows(Source, IsWanted)', 'Table.TransformColumnTypes(Source, Types)',
                           'Table.RenameColumns(Source, List.Zip({Old, New}))', 'Table.RenameColumns(Source, {{"a", NewName}})',
                           'Sql.Database(Server & ".contoso.com", "db")', 'Table.Sort(Source, each [A])',
                           'Table.FirstN(Source, each [A] > 1)', 'Table.NestedJoin(Source, Keys, Other, Keys, "x")',
                           'Table.Combine({Source, Table.Skip(Other, 1)})', 'Source{[Name=Wanted]}[Data]', 'Source{Index}[Data]',
                           'Custom.Connector("x")', 'if A then B else C', '1 + 2', 'Table.Group(Source, Keys, Aggregations)',
                           'Csv.Document(Binary.Combine(Files))', 'SomeFunction(Source)', 'Table.ReplaceValue(Source, each [a], each [b], Replacer.ReplaceValue, {"c"})'):
            self.assertEqual(says(expression), "", expression)
        # ...but the function a step calls is still named, as written.
        reading = steps('let Source = 1, Step = Table.RemoveColumns(Source, ColumnsToDrop) in Step')
        self.assertEqual((reading.steps[1].call, reading.steps[1].description), ("Table.RemoveColumns", ""))
        self.assertEqual(steps('let Source = #table({"A"}, {}) in Source').steps[0].call, "#table")
        self.assertEqual(steps("let Source = 1 + 2 in Source").steps[0].call, "")

    def test_long_lists_are_cut_short_and_long_conditions_are_not_quoted(self):
        columns = ", ".join(f'"C{i}"' for i in range(10))
        self.assertEqual(says(f"Table.RemoveColumns(Source, {{{columns}}})"), "Removes 10 columns: C0, C1, C2, C3, C4, C5 and 4 more")
        condition = " and ".join(f'[Column{i}] <> "value {i}"' for i in range(12))
        self.assertEqual(says(f"Table.SelectRows(Source, each {condition})"), "Keeps rows that meet a condition")

    def test_a_description_is_built_from_the_text_and_claims_nothing_about_a_run(self):
        described = [step.description for step in steps(LONG).steps]
        self.assertEqual(described, [
            "Connects to SQL Server: server srv, database dw",
            "Navigates to dbo.Orders",
            "Sets the data type of 3 columns: Amount (fixed decimal number), Placed (date), Note, with comma (text)",
            "Adds the column Net, as decimal number",
            'Keeps rows where [Note] <> "let x = 1, y = 2 in x" and [Net] > 0',
            "Removes duplicate rows"])
        for text in described:
            for claim in ("rows were", "returned", "succeeded", "failed", "took", "loaded "):
                self.assertNotIn(claim, text)


if __name__ == "__main__":
    unittest.main()
