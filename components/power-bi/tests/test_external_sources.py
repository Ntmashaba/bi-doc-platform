import unittest
from pbidocgen.m_sources import Tracer, materialize
from pbidocgen.source_objects import build_source_objects
from pbidocgen.primary_sources import build_primary_sources


class ExternalSourceTests(unittest.TestCase):
    def rows(self, code, definitions=None):
        return materialize(Tracer(definitions).trace(code, 'Final'))

    def test_file_reader_and_sheet_are_one_external_input(self):
        rows = self.rows('let S=Excel.Workbook(File.Contents("C:\\Data\\Budget.xlsx"),null,true), T=S{[Item="Budget",Kind="Sheet"]}[Data] in T')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['object'], 'Budget.xlsx')
        self.assertEqual(rows[0]['location'], 'C:\\Data\\Budget.xlsx')
        self.assertEqual(rows[0]['status'], 'Resolved')
        self.assertEqual(rows[0]['primaryQueries'], ['Final'])

    def test_sharepoint_file_navigation_keeps_absolute_file_not_sheet(self):
        code = 'let S=SharePoint.Files("https://tenant.sharepoint.com/sites/BI"), B=S{[Name="Budget.xlsx",#"Folder Path"="https://tenant.sharepoint.com/sites/BI/Documents/"]}[Content], W=Excel.Workbook(B), T=W{[Item="Budget",Kind="Sheet"]}[Data] in T'
        rows = self.rows(code)
        self.assertEqual(len(rows), 1)
        # One document picked from a library is a SharePoint file, as with Web.Contents.
        self.assertEqual(rows[0]['sourceType'], 'SharePoint file')
        self.assertEqual(rows[0]['location'], 'https://tenant.sharepoint.com/sites/BI/Documents/Budget.xlsx')
        self.assertEqual(rows[0]['object'], 'Budget.xlsx')
        self.assertEqual(rows[0]['status'], 'Resolved')
        unquoted = self.rows(code.replace('#"Folder Path"', 'Folder Path'))
        self.assertEqual(unquoted[0]['location'], rows[0]['location'])

    def test_sharepoint_contents_hierarchical_path(self):
        rows = self.rows('let S=SharePoint.Contents("https://tenant.sharepoint.com/sites/BI"), D=S{[Name="Documents"]}[Content], B=D{[Name="Budget.xlsx"]}[Content] in Excel.Workbook(B)')
        self.assertEqual(rows[0]['location'], 'https://tenant.sharepoint.com/sites/BI/Documents/Budget.xlsx')
        self.assertEqual(rows[0]['object'], 'Budget.xlsx')

    def test_sharepoint_exact_filter_and_ambiguous_filename(self):
        code = 'let S=SharePoint.Files("https://tenant.sharepoint.com/sites/BI") in Table.SelectRows(S, each [Name] = "Budget.xlsx" and [Folder Path] = "https://tenant.sharepoint.com/sites/BI/Documents/")'
        rows = self.rows(code)
        self.assertEqual(rows[0]['location'], 'https://tenant.sharepoint.com/sites/BI/Documents/Budget.xlsx')
        self.assertTrue(rows[0]['preparationEffects'])
        rows = self.rows('SharePoint.Files("https://tenant.sharepoint.com/sites/BI"){[Name="Budget.xlsx"]}[Content]')
        self.assertEqual(rows[0]['location'], 'https://tenant.sharepoint.com/sites/BI')
        self.assertEqual(rows[0]['status'], 'Partial')
        self.assertIn('containing folder', str(rows[0]['notes']))

    def test_or_filter_does_not_claim_a_single_file(self):
        rows = self.rows('Table.SelectRows(SharePoint.Files("https://tenant.sharepoint.com/sites/BI"), each [Name]="A.xlsx" or [Name]="B.xlsx")')
        self.assertEqual(rows[0]['object'], '')
        self.assertEqual(rows[0]['location'], 'https://tenant.sharepoint.com/sites/BI')

    def test_sharepoint_list_identity(self):
        rows = self.rows('SharePoint.Tables("https://tenant.sharepoint.com/sites/BI"){[Id="list-guid"]}[Items]')
        self.assertEqual(rows[0]['sourceType'], 'SharePoint list')
        self.assertEqual(rows[0]['object'], 'list-guid')
        self.assertEqual(rows[0]['location'], 'https://tenant.sharepoint.com/sites/BI')

    def test_folder_collection_does_not_invent_members(self):
        rows = self.rows('Folder.Files("\\\\server\\share\\inputs")')
        self.assertEqual(rows[0]['location'], '\\\\server\\share\\inputs')
        self.assertEqual(rows[0]['object'], '')
        self.assertEqual(rows[0]['status'], 'Unresolved')

    def test_web_relative_path_and_url_parameters(self):
        rows = self.rows('Json.Document(Web.Contents("https://user:secret@example.org/api?token=secret",[RelativePath="sales",Query=[key="secret"],Headers=[Authorization="secret"]]))')
        self.assertEqual(rows[0]['location'], 'https://example.org/api/sales')
        self.assertEqual(rows[0]['object'], 'https://example.org/api/sales')
        self.assertNotIn('secret', str(rows))
        self.assertEqual(rows[0]['status'], 'Partial')

    def test_dynamic_web_location_and_relative_path_are_explicit(self):
        rows = self.rows('Web.Contents(RuntimeUrl)')
        self.assertEqual(rows[0]['sourceType'], 'Web / API')
        self.assertEqual(rows[0]['status'], 'Unresolved')
        rows = self.rows('Web.Contents("https://example.org/api",[RelativePath=RuntimePath])')
        self.assertEqual(rows[0]['location'], 'https://example.org/api')
        self.assertEqual(rows[0]['status'], 'Partial')

    def test_odata_and_azure_storage(self):
        for fn, target in [('OData.Feed','https://example.org/odata'),
                           ('AzureStorage.BlobContents','https://a.blob.core.windows.net/c/file.csv'),
                           ('AzureStorage.DataLakeContents','https://a.dfs.core.windows.net/c/file.csv')]:
            with self.subTest(fn=fn):
                rows = self.rows(fn+'("'+target+'")')
                self.assertEqual(rows[0]['location'], target)
                self.assertEqual(rows[0]['status'], 'Resolved')

    def test_merge_and_removed_columns_retain_potential_input(self):
        defs = {'Lookup': 'Csv.Document(File.Contents("C:\\lookup.csv"))'}
        rows = self.rows('let S=Sql.Database("sql","db",[Query="SELECT * FROM dbo.Sales"]), J=Table.NestedJoin(S,{"ID"},Lookup,{"ID"},"L"), R=Table.RemoveColumns(J,{"L"}) in R', defs)
        self.assertEqual({r['sourceType'] for r in rows}, {'SQL Server','CSV file'})
        for row in rows:
            self.assertTrue(any('Merge/join' in e for e in row['preparationEffects']))
            self.assertTrue(any('Column selection' in e for e in row['preparationEffects']))

    def test_shared_connection_with_parameters_is_external_not_staging(self):
        rows = self.rows('Table.Distinct(Stage)', {'Root':'"C:\\inputs"', 'Stage':'Csv.Document(File.Contents(Root & "\\sales.csv"))'})
        self.assertEqual(rows[0]['primaryQueries'], ['Stage'])
        self.assertEqual(rows[0]['location'], 'C:\\inputs\\sales.csv')


class SourceUsageTests(unittest.TestCase):
    def inventory(self, usage=None, report=True, issues=None, code='File.Contents("C:\\data.csv")', expressions=None):
        model = dict(tables=[dict(name='Final', partitions=[dict(name='Import',type='m',expression=code)])],
                     expressions=[dict(name=k,kind='m',expression=v) for k,v in (expressions or {}).items()])
        r = dict(name='Report') if report else None
        columns = dict(tablePages=[dict(table='Final', **usage)]) if usage else None
        return build_primary_sources(model, r, build_source_objects(model,r,columns), issues)

    def test_model_present_but_not_reported_is_not_safe_to_delete(self):
        data=self.inventory(dict(page='',pageId='',scope='No page usage detected',usage='No page usage detected'))
        row=data['rows'][0]
        self.assertEqual(row['reportingStatus'], 'No reporting usage found')
        self.assertEqual(row['dependencyStatus'], ['Model partition defined'])
        self.assertIn('unknown', row['runtimeStatus'])
        self.assertIn('No deletion verdict', row['removalAssessment'])

    def test_downstream_direct_use_is_only_potential_source_contribution(self):
        data=self.inventory(dict(page='Sales',pageId='p1',scope='Page',usage='Direct',kinds=['Direct']))
        row=data['rows'][0]
        self.assertEqual(row['reportingStatus'], 'Potential reporting dependency')
        self.assertEqual(row['usageConfidence'], 'Possible')
        self.assertIn('not proven', row['usageEvidence'][0])
        self.assertEqual(row['pageId'], 'p1')

    def test_relationship_only_is_distinct_from_direct_use(self):
        data=self.inventory(dict(page='Sales',pageId='p1',scope='Page',usage='Possible relationship dependency',kinds=['Possible relationship dependency']))
        self.assertEqual(data['rows'][0]['reportingStatus'], 'Possible model dependency')

    def test_missing_report_or_incomplete_analysis_is_unknown(self):
        self.assertEqual(self.inventory(report=False)['rows'][0]['reportingStatus'], 'Usage unresolved')
        data=self.inventory(dict(page='',pageId='',scope='No page usage detected',usage='No page usage detected'), issues=['Missing report page'])
        self.assertEqual(data['rows'][0]['reportingStatus'], 'Usage unresolved')

    def test_staging_vs_defined_only(self):
        data=self.inventory(dict(page='Sales',pageId='p1',scope='Page',usage='Direct'), code='Stage',
                            expressions={'Stage':'File.Contents("C:\\data.csv")','Dormant':'File.Contents("C:\\other.csv")'})
        stage=next(r for r in data['rows'] if r['primaryQueries']==['Stage'])
        dormant=next(r for r in data['rows'] if r['primaryQueries']==['Dormant'])
        self.assertEqual(stage['dependencyStatus'], ['Preparation dependency to model'])
        self.assertEqual(dormant['reportingStatus'], 'No model consumer found')
        self.assertEqual(dormant['pageId'], '')

    def test_unknown_custom_connector_remains_in_export_rows(self):
        data=self.inventory(code='CustomPlatform.Fetch("account")')
        self.assertEqual(len(data['rows']), 1)
        # The connector is named rather than reported as Unknown, but stays unresolved.
        self.assertEqual(data['rows'][0]['sourceType'], 'CustomPlatform.Fetch (unrecognised connector)')
        self.assertEqual(data['rows'][0]['status'], 'Unresolved')
        self.assertEqual(data['rows'][0]['reportingStatus'], 'Usage unresolved')
        self.assertEqual(len(data['unresolved']), 1)
