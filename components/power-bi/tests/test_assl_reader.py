"""Tabular models stored as XML: Model.bim and SSMS CREATE scripts at compatibility level 1100 and 1103.

The fixture is written in the shape of Microsoft's 1103 scripts (github.com/microsoft/Analysis-Services,
AlmToolkit/BismNormalizer.Tests/Test1103_Source.xmla): the same namespaces and prefixes, a Dimension per table,
partitions in the cube's measure groups, measures in the MDX script and relationships under the dimension.
"""
import json
import tempfile
import unittest
from pathlib import Path

from pbidocgen.assl_reader import read_assl_model
from pbidocgen.model_parser import parse_model
from pbidocgen.source_objects import build_source_objects

NS = ('xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
      'xmlns:ddl2="http://schemas.microsoft.com/analysisservices/2003/engine/2" '
      'xmlns:ddl200_200="http://schemas.microsoft.com/analysisservices/2010/engine/200/200" '
      'xmlns:ddl300="http://schemas.microsoft.com/analysisservices/2011/engine/300" '
      'xmlns:ddl300_300="http://schemas.microsoft.com/analysisservices/2011/engine/300/300"')
SECRETS = ("hunter2", "svc_sales_reader", "svc_ssas", "CORP\\\\alice")


def attribute(key, name=None, data_type="BigInt", table="Sales_1", extra="", binding=None):
    binding = binding or f'<Source xsi:type="ColumnBinding"><TableID>{table}</TableID><ColumnID>{key}</ColumnID></Source>'
    return (f'<Attribute><ID>{key}</ID><Name>{name or key}</Name><KeyColumns><KeyColumn><DataType>{data_type}</DataType>'
            f'<DataSize>-1</DataSize>{binding}</KeyColumn></KeyColumns><OrderBy>Key</OrderBy>{extra}</Attribute>')


ROW_NUMBER = ('<Attribute><ID>__XL_RowNumber</ID><Name>__XL_RowNumber</Name><Type valuens="ddl200_200">RowNumber</Type>'
              '<Usage>Key</Usage><KeyColumns><KeyColumn><DataType>Integer</DataType>'
              '<Source xsi:type="ddl200_200:RowNumberBinding" /></KeyColumn></KeyColumns></Attribute>')


def dimension(key, name, attributes, extra=""):
    return (f'<Dimension><ID>{key}</ID><Name>{name}</Name><Source xsi:type="DataSourceViewBinding">'
            f'<DataSourceViewID>Sandbox</DataSourceViewID></Source><StorageMode valuens="ddl200_200">InMemory</StorageMode>'
            f'<Attributes>{ROW_NUMBER}{"".join(attributes)}</Attributes>{extra}</Dimension>')


def relationship(key, from_table, from_column, to_table, to_column):
    def end(tag, table, column, multiplicity):
        return (f'<{tag}><Multiplicity>{multiplicity}</Multiplicity><DimensionID>{table}</DimensionID>'
                f'<Attributes><Attribute><AttributeID>{column}</AttributeID></Attribute></Attributes></{tag}>')
    return (f'<ddl300_300:Relationship><ID>{key}</ID>{end("FromRelationshipEnd", from_table, from_column, "Many")}'
            f'{end("ToRelationshipEnd", to_table, to_column, "One")}</ddl300_300:Relationship>')


def partition(key, name, source, query, extra=""):
    return (f'<Partition><ID>{key}</ID><Name>{name}</Name><Source xsi:type="QueryBinding"><DataSourceID>{source}</DataSourceID>'
            f'<QueryDefinition>{query}</QueryDefinition></Source>{extra}</Partition>')


def measure_group(table, partitions, references=""):
    return (f'<MeasureGroup><ID>{table}</ID><Name>{table}</Name><Dimensions>'
            f'<Dimension xsi:type="DegenerateMeasureGroupDimension"><CubeDimensionID>{table}</CubeDimensionID></Dimension>'
            f'{references}</Dimensions><Partitions>{"".join(partitions)}</Partitions></MeasureGroup>')


def reference(table, relationship_id):
    return (f'<Dimension xsi:type="ReferenceMeasureGroupDimension"><CubeDimensionID>{table}</CubeDimensionID>'
            f'<Materialization>Regular</Materialization><ddl300:RelationshipID>{relationship_id}</ddl300:RelationshipID></Dimension>')


MDX = """CALCULATE;
CREATE MEMBER CURRENTCUBE.Measures.[__No measures defined] AS 1, VISIBLE = 0;
ALTER CUBE CURRENTCUBE UPDATE DIMENSION Measures, Default_Member = [__No measures defined];
----------------------------------------------------------
-- PowerPivot measures command (do not modify manually) --
----------------------------------------------------------


CREATE MEASURE 'Sales'[Total Amount]=SUM('Sales'[Amount]);
CREATE MEASURE 'Sales'[Share]=DIVIDE([Total Amount],
    CALCULATE([Total Amount], ALL('Region')));
CREATE MEASURE 'Bob''s Region'[Regions [all]]]=COUNTROWS('Bob''s Region');
CREATE KPI CURRENTCUBE.[Total Amount] AS Measures.[Total Amount], ASSOCIATED_MEASURE_GROUP = 'Sales', GOAL = 100;
"""


def database(level="1103", engine="<StorageEngineUsed>InMemory</StorageEngineUsed>"):
    sales = dimension("Sales_1", "Sales", [
        attribute("RegionKey"), attribute("OldRegionKey", "Old Region Key"),
        attribute("Amount", data_type="Currency", extra="<AttributeHierarchyVisible>false</AttributeHierarchyVisible>"),
        attribute("OrderDate", "Order Date", data_type="Date"),
        attribute("Margin", data_type="Double", binding='<Source xsi:type="ddl200_200:ExpressionBinding">'
                  "<Expression>'Sales'[Amount] * 0.2</Expression></Source>"),
    ], extra='<DimensionPermissions><DimensionPermission><ID>P</ID><RoleID>Role</RoleID><Read>Allowed</Read>'
             "<AllowedRowsExpression>'Sales'[RegionKey] = 1</AllowedRowsExpression></DimensionPermission></DimensionPermissions>"
             f'<ddl300_300:Relationships>{relationship("rel-active", "Sales_1", "RegionKey", "Region_2", "RegionKey")}'
             f'{relationship("rel-inactive", "Sales_1", "OldRegionKey", "Region_2", "RegionKey")}</ddl300_300:Relationships>')
    region = dimension("Region_2", "Bob's Region", [
        attribute("RegionKey", table="Region_2"),
        attribute("RegionName", "Region Name", "WChar", "Region_2", "<OrderByAttributeID>SortOrder</OrderByAttributeID>"),
        attribute("SortOrder", "Sort Order", table="Region_2"),
    ], extra='<Hierarchies><Hierarchy><ID>Geo</ID><Name>Geography</Name><Levels><Level><ID>L1</ID><Name>Region</Name>'
             '<SourceAttributeID>RegionName</SourceAttributeID></Level></Levels></Hierarchy></Hierarchies>')
    tariff = dimension("Tariff_3", "Tariff", [attribute("Code", data_type="WChar", table="Tariff_3")])
    lookup = dimension("Lookup_4", "Lookup", [attribute("Code", data_type="WChar", table="Lookup_4")])
    cube = ('<Cube><ID>Model</ID><Name>Model</Name><Dimensions>'
            + "".join(f'<Dimension><ID>{key}</ID><Name>{key}</Name><DimensionID>{key}</DimensionID>{extra}</Dimension>'
                      for key, extra in (("Sales_1", ""), ("Region_2", ""), ("Tariff_3", "<Visible>false</Visible>"), ("Lookup_4", "")))
            + "</Dimensions><MeasureGroups>"
            + measure_group("Sales_1", [
                partition("p1", "Sales 2024", "ds-sql", "SELECT [dbo].[FactSales].* FROM [dbo].[FactSales] WHERE [Yr] = 2024"),
                partition("p2", "Sales 2025", "ds-sql", "SELECT f.* FROM dbo.FactSales2025 f JOIN ref.Calendar c ON c.Id = f.DateId",
                          '<StorageMode valuens="ddl200_200">DirectQuery</StorageMode>')],
                reference("Region_2", "rel-active"))
            + measure_group("Region_2", [partition("p3", "Region", "ds-sql", "SELECT * FROM [dbo].[DimRegion]")])
            + measure_group("Tariff_3", [partition("p4", "Tariff", "ds-ora", "select * from billing.tariff")])
            + f"</MeasureGroups><MdxScripts><MdxScript><ID>MdxScript</ID><Commands><Command><Text>{MDX}</Text></Command></Commands>"
              "<CalculationProperties><CalculationProperty><CalculationReference>[Total Amount]</CalculationReference>"
              "<CalculationType>Member</CalculationType><FormatString>'#,0.00'</FormatString><Visible>false</Visible>"
              "</CalculationProperty></CalculationProperties></MdxScript></MdxScripts></Cube>")
    return (f'<Database {NS}><ID>Finance</ID><Name>Finance</Name><CompatibilityLevel>{level}</CompatibilityLevel>{engine}'
            f'<Dimensions>{sales}{region}{tariff}{lookup}</Dimensions><Cubes>{cube}</Cubes>'
            '<DataSources><DataSource xsi:type="RelationalDataSource"><ID>ds-sql</ID><Name>SqlServer sqlprod01 SalesDW</Name>'
            '<ConnectionString>Provider=SQLNCLI11;Data Source=sqlprod01;Initial Catalog=SalesDW;User ID=svc_sales_reader;'
            'Password=hunter2</ConnectionString><ImpersonationInfo><ImpersonationMode>ImpersonateAccount</ImpersonationMode>'
            '<Account>CORP\\svc_ssas</Account></ImpersonationInfo></DataSource>'
            '<DataSource xsi:type="RelationalDataSource"><ID>ds-ora</ID><Name>Oracle ORAPRD</Name>'
            '<ConnectionString>Provider=OraOLEDB.Oracle;Data Source=ORAPRD;User ID=svc_sales_reader;Password=hunter2</ConnectionString>'
            '</DataSource></DataSources>'
            '<DataSourceViews><DataSourceView><ID>Sandbox</ID><DataSourceID>ds-sql</DataSourceID><Schema>'
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:msprop="urn:schemas-microsoft-com:xml-msprop">'
            '<xs:element name="Lookup_4" msprop:DbTableName="Lookup" msprop:QueryDefinition="SELECT * FROM [ref].[Lookup]" />'
            '</xs:schema></Schema></DataSourceView></DataSourceViews>'
            '<Roles><Role><ID>Role</ID><Name>Readers</Name><Members><Member><Name>CORP\\alice</Name></Member></Members></Role>'
            '<Role><ID>Role2</ID><Name>Processors</Name></Role></Roles>'
            '<DatabasePermissions><DatabasePermission><ID>a</ID><RoleID>Role</RoleID><Read>Allowed</Read></DatabasePermission>'
            '<DatabasePermission><ID>b</ID><RoleID>Role2</RoleID><Process>true</Process><Read>Allowed</Read></DatabasePermission>'
            '</DatabasePermissions></Database>')


def create(db=None):
    return ('<Create xmlns="http://schemas.microsoft.com/analysisservices/2003/engine"><ObjectDefinition>'
            f'{db or database()}</ObjectDefinition></Create>')


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def write(self, text, name="Finance.xmla", encoding="utf-8"):
        path = self.tmp / name
        path.write_text(text, encoding=encoding)
        return path

    def load(self, text=None, name="Finance.xmla", encoding="utf-8"):
        return parse_model(self.write(text or create(), name, encoding))

    def sources(self, model):
        return {(r["table"], r["partition"], r["sourceType"], r["server"], r["database"], r["schema"], r["object"], r["status"])
                for r in build_source_objects(model, None, None)}


class Sources(Case):
    def test_every_partition_resolves_to_its_data_source_and_tables(self):
        model = self.load()
        self.assertEqual((model["compatibilityLevel"], model["sourceFormat"], model["name"]), (1103, "ASSL", "Finance"))
        sql = ("SQL Server", "sqlprod01", "SalesDW")
        self.assertEqual({row for row in self.sources(model) if row[0] != "Tariff"}, {
            ("Sales", "Sales 2024", *sql, "dbo", "FactSales", "Resolved"),
            ("Sales", "Sales 2025", *sql, "dbo", "FactSales2025", "Resolved"),
            ("Sales", "Sales 2025", *sql, "ref", "Calendar", "Resolved"),
            ("Bob's Region", "Region", *sql, "dbo", "DimRegion", "Resolved"),
            ("Lookup", "Lookup", *sql, "ref", "Lookup", "Resolved")})
        tariff = next(row for row in self.sources(model) if row[0] == "Tariff")
        self.assertEqual(tariff[2:7], ("Oracle", "ORAPRD", "", "BILLING", "TARIFF"))     # Oracle folds unquoted names

    def test_a_table_without_a_partition_takes_its_query_from_the_data_source_view(self):
        lookup = next(t for t in self.load()["tables"] if t["name"] == "Lookup")
        self.assertEqual([(p["name"], p["type"], p["expression"]) for p in lookup["partitions"]],
                         [("Lookup", "query", "SELECT * FROM [ref].[Lookup]")])

    def test_data_sources_keep_identity_and_nothing_that_authenticates(self):
        model = self.load()
        self.assertEqual([(d["name"], d["sourceType"], d["server"], d["database"]) for d in model["dataSources"]],
                         [("SqlServer sqlprod01 SalesDW", "SQL Server", "sqlprod01", "SalesDW"),
                          ("Oracle ORAPRD", "Oracle", "ORAPRD", None)])
        text = json.dumps([model, build_source_objects(model, None, None)])
        for secret in SECRETS:
            self.assertNotIn(secret, text)

    def test_directquery_partitions_keep_their_mode(self):
        sales = next(t for t in self.load()["tables"] if t["name"] == "Sales")
        self.assertEqual([p["mode"] for p in sales["partitions"]], ["import", "directQuery"])


class Model(Case):
    def test_tables_and_columns(self):
        tables = {t["name"]: t for t in self.load()["tables"]}
        self.assertEqual(list(tables), ["Sales", "Bob's Region", "Tariff", "Lookup"])
        self.assertTrue(tables["Tariff"]["isHidden"])
        sales = {c["name"]: c for c in tables["Sales"]["columns"]}
        self.assertEqual(list(sales), ["RegionKey", "Old Region Key", "Amount", "Order Date", "Margin"])     # no row number
        self.assertEqual((sales["Old Region Key"]["sourceColumn"], sales["Old Region Key"]["dataType"]), ("OldRegionKey", "int64"))
        self.assertEqual((sales["Amount"]["dataType"], sales["Amount"]["isHidden"]), ("decimal", True))
        self.assertEqual(sales["Order Date"]["dataType"], "dateTime")
        self.assertEqual((sales["Margin"]["isCalculated"], sales["Margin"]["expression"]), (True, "'Sales'[Amount] * 0.2"))
        region = {c["name"]: c for c in tables["Bob's Region"]["columns"]}
        self.assertEqual(region["Region Name"]["sortByColumn"], "Sort Order")
        self.assertEqual(tables["Bob's Region"]["hierarchies"][0]["levels"], ["Region Name"])

    def test_measures_come_from_the_mdx_script(self):
        measures = {(m["table"], m["name"]): m for m in self.load()["measures"]}
        self.assertEqual(set(measures), {("Sales", "Total Amount"), ("Sales", "Share"), ("Bob's Region", "Regions [all]")})
        self.assertEqual(measures["Sales", "Total Amount"]["expression"], "SUM('Sales'[Amount])")
        self.assertEqual((measures["Sales", "Total Amount"]["formatString"], measures["Sales", "Total Amount"]["isHidden"]),
                         ("#,0.00", True))
        self.assertEqual(measures["Sales", "Share"]["expression"],
                         "DIVIDE([Total Amount],\n    CALCULATE([Total Amount], ALL('Region')))")
        self.assertEqual(measures["Bob's Region", "Regions [all]"]["expression"], "COUNTROWS('Bob''s Region')")

    def test_a_kpi_is_reported_as_not_read(self):
        messages = [w["message"] for w in self.load()["warnings"] if w["category"] == "Unreadable definition"]
        self.assertEqual(messages, ["1 KPI definition(s) in the model were not read; their base measures were."])

    def test_relationships_and_which_of_them_is_active(self):
        relationships = {(r["fromTable"], r["fromColumn"], r["toTable"], r["toColumn"]): r for r in self.load()["relationships"]}
        self.assertEqual({key: (r["isActive"], r["fromCardinality"], r["toCardinality"]) for key, r in relationships.items()}, {
            ("Sales", "RegionKey", "Bob's Region", "RegionKey"): (True, "many", "one"),
            ("Sales", "Old Region Key", "Bob's Region", "RegionKey"): (False, "many", "one")})

    def test_roles_permissions_and_row_filters_without_members(self):
        roles = {r["name"]: r for r in self.load()["roles"]}
        self.assertEqual({name: r["modelPermission"] for name, r in roles.items()}, {"Readers": "read", "Processors": "readRefresh"})
        self.assertEqual(roles["Readers"]["tablePermissions"], [{"table": "Sales", "filterExpression": "'Sales'[RegionKey] = 1"}])


class Files(Case):
    def test_a_model_bim_named_file_and_other_wrappers_read_the_same(self):
        expected = self.sources(self.load())
        soap = ('<Batch xmlns="http://schemas.microsoft.com/analysisservices/2003/engine"><Alter AllowCreate="true" '
                f'ObjectExpansion="ExpandFull"><Object><DatabaseID>Finance</DatabaseID></Object><ObjectDefinition>{database()}'
                '</ObjectDefinition></Alter></Batch>')
        bare = database().replace("<Database ", '<Database xmlns="http://schemas.microsoft.com/analysisservices/2003/engine" ', 1)
        for name, text in (("Model.bim", create()), ("alter.xmla", soap), ("bare.xml", bare)):
            self.assertEqual(self.sources(self.load(text, name)), expected, name)

    def test_utf16_with_a_declaration_and_a_utf8_bom(self):
        expected = self.sources(self.load())
        declared = '<?xml version="1.0" encoding="utf-16"?>\n' + create()
        self.assertEqual(self.sources(self.load(declared, "utf16.xmla", "utf-16")), expected)
        self.assertEqual(self.sources(self.load(create(), "bom.xmla", "utf-8-sig")), expected)

    def test_level_1100_reads_the_same_way(self):
        self.assertEqual(self.load(create(database(level="1100")))["compatibilityLevel"], 1100)

    def test_a_multidimensional_cube_is_refused_by_name(self):
        with self.assertRaisesRegex(ValueError, "multidimensional database.*not a tabular model"):
            self.load(create(database(engine="").replace('<StorageMode valuens="ddl200_200">InMemory</StorageMode>', "")))

    def test_xml_without_a_database_definition_says_what_to_script(self):
        process = ('<Batch xmlns="http://schemas.microsoft.com/analysisservices/2003/engine"><Process><Object>'
                   '<DatabaseID>Finance</DatabaseID></Object><Type>ProcessFull</Type></Process></Batch>')
        with self.assertRaisesRegex(ValueError, "does not define an Analysis Services database.*Script Database as"):
            self.load(process, "process.xmla")

    def test_a_document_type_declaration_is_refused(self):
        bomb = '<!DOCTYPE x [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>\n' + create()
        with self.assertRaisesRegex(ValueError, "declares a document type or entities"):
            self.load(bomb, "bomb.xmla")

    def test_broken_xml_is_reported_as_such(self):
        with self.assertRaisesRegex(ValueError, "not well-formed XML"):
            self.load(create()[:-20], "cut.xmla")

    def test_the_reader_returns_a_model_bim_shaped_document(self):
        doc = read_assl_model(self.write(create()))
        self.assertEqual((doc["name"], doc["compatibilityLevel"], sorted(doc["model"])),
                         ("Finance", 1103, ["dataSources", "relationships", "roles", "tables"]))
        sales = doc["model"]["tables"][0]
        self.assertEqual(sales["partitions"][0]["source"], {
            "type": "query", "query": "SELECT [dbo].[FactSales].* FROM [dbo].[FactSales] WHERE [Yr] = 2024",
            "dataSource": "SqlServer sqlprod01 SalesDW"})


if __name__ == "__main__":
    unittest.main()
