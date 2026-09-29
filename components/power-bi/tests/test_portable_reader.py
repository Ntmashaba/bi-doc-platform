"""portable.model_document over scripted metadata databases: grouping, ordering and the empty-model guard."""
import sqlite3
import unittest
from unittest import mock

from pbidocgen import input_limits, portable

SCHEMA = '''
CREATE TABLE Model (Name, DefaultMode, Culture);
CREATE TABLE "Table" (ID, Name, Description, IsHidden, DataCategory, LineageTag, SystemFlags, CalculationGroupID);
CREATE TABLE Column (ID, TableID, Type, SortByColumnID, ExplicitName, InferredName, ExplicitDataType, InferredDataType);
CREATE TABLE Partition (ID, TableID, Name, Type, Mode, QueryDefinition, DataSourceID);
CREATE TABLE Measure (ID, TableID, Name, Expression, FormatString, IsHidden);
CREATE TABLE Annotation (ObjectID, Name, Value);
CREATE TABLE Hierarchy (ID, TableID, Name);
CREATE TABLE Level (ID, HierarchyID, Ordinal, Name, ColumnID);
CREATE TABLE Role (ID, Name, ModelPermission);
CREATE TABLE TablePermission (ID, RoleID, TableID, FilterExpression, MetadataPermission);
CREATE TABLE CalculationGroup (ID, Precedence);
CREATE TABLE CalculationItem (ID, CalculationGroupID, Name, Expression, Ordinal);
'''


def document(*statements, schema=SCHEMA):
    db = sqlite3.connect(':memory:')
    db.executescript(schema)
    for statement in statements:
        db.execute(statement)
    db.commit()
    raw = db.serialize()
    db.close()
    with mock.patch.object(portable, '_metadata', side_effect=lambda *a, **k: input_limits.open_metadata(raw)):
        return portable.model_document('model.abf')['model']


MODEL = "INSERT INTO Model VALUES ('M', 0, 'en-US')"


def table(tid, name, group=None):
    return f"INSERT INTO \"Table\" VALUES ({tid}, '{name}', NULL, 0, NULL, NULL, 0, {group or 'NULL'})"


def column(cid, tid, name):
    return f"INSERT INTO Column VALUES ({cid}, {tid}, 1, NULL, '{name}', NULL, 2, 2)"


class Grouping(unittest.TestCase):
    def test_each_table_gets_only_its_own_parts_in_row_order(self):
        model = document(
            MODEL, table(1, 'A'), table(2, 'B'),
            column(10, 1, 'a1'), column(11, 2, 'b1'), column(12, 1, 'a2'), column(13, 2, 'b2'),
            "INSERT INTO Measure VALUES (1, 2, 'mB', '1', '0', 0)", "INSERT INTO Measure VALUES (2, 1, 'mA', '2', '0', 0)",
            "INSERT INTO Partition VALUES (1, 1, 'pA', 4, 0, 'let x=1 in x', NULL)",
            "INSERT INTO Partition VALUES (2, 2, 'pB', 4, 1, 'let y=1 in y', NULL)",
            "INSERT INTO Annotation VALUES (1, 'note', 'for A')", "INSERT INTO Annotation VALUES (2, 'note', 'for B')",
            "INSERT INTO Annotation VALUES (99, 'note', 'for nobody')")
        by = {t['name']: t for t in model['tables']}
        self.assertEqual([c['name'] for c in by['A']['columns']], ['a1', 'a2'])
        self.assertEqual([c['name'] for c in by['B']['columns']], ['b1', 'b2'])
        self.assertEqual([m['name'] for m in by['A']['measures']], ['mA'])
        self.assertEqual([(p['name'], p['mode']) for p in by['B']['partitions']], [('pB', 'directQuery')])
        self.assertEqual(by['A']['annotations'], [{'name': 'note', 'value': 'for A'}])
        self.assertEqual(by['B']['annotations'], [{'name': 'note', 'value': 'for B'}])

    def test_many_tables_keep_their_own_columns(self):
        statements = [MODEL]
        for tid in range(1, 201):
            statements.append(table(tid, f'T{tid}'))
            statements += [column(tid * 10 + i, tid, f'T{tid}.c{i}') for i in range(3)]
        model = document(*statements)
        self.assertEqual(len(model['tables']), 200)
        for t in model['tables']:
            self.assertEqual([c['name'] for c in t['columns']], [f"{t['name']}.c{i}" for i in range(3)])

    def test_hierarchy_levels_follow_their_ordinal_and_ties_keep_row_order(self):
        model = document(
            MODEL, table(1, 'A'), column(10, 1, 'year'), column(11, 1, 'month'), column(12, 1, 'day'), column(13, 1, 'hour'),
            "INSERT INTO Hierarchy VALUES (1, 1, 'Date')", "INSERT INTO Hierarchy VALUES (2, 1, 'Other')",
            "INSERT INTO Level VALUES (1, 1, 2, 'Day', 12)", "INSERT INTO Level VALUES (2, 1, 0, 'Year', 10)",
            "INSERT INTO Level VALUES (3, 1, 1, 'Month', 11)", "INSERT INTO Level VALUES (4, 2, 0, 'First', 13)",
            "INSERT INTO Level VALUES (5, 2, 0, 'Second', 10)")
        levels = {h['name']: [(lv['name'], lv['column']) for lv in h['levels']] for h in model['tables'][0]['hierarchies']}
        self.assertEqual(levels['Date'], [('Year', 'year'), ('Month', 'month'), ('Day', 'day')])
        self.assertEqual(levels['Other'], [('First', 'hour'), ('Second', 'year')])       # tie: original row order

    def test_a_level_without_an_ordinal_sorts_first_instead_of_failing(self):
        model = document(
            MODEL, table(1, 'A'), column(10, 1, 'a'), column(11, 1, 'b'), "INSERT INTO Hierarchy VALUES (1, 1, 'H')",
            "INSERT INTO Level VALUES (1, 1, 1, 'Later', 10)", "INSERT INTO Level VALUES (2, 1, NULL, 'Unordered', 11)")
        self.assertEqual([lv['name'] for lv in model['tables'][0]['hierarchies'][0]['levels']], ['Unordered', 'Later'])

    def test_roles_get_only_their_own_table_permissions(self):
        model = document(
            MODEL, table(1, 'A'), table(2, 'B'),
            "INSERT INTO Role VALUES (1, 'Readers', 2)", "INSERT INTO Role VALUES (2, 'Admins', 5)",
            "INSERT INTO TablePermission VALUES (1, 1, 1, '[x]=1', 0)", "INSERT INTO TablePermission VALUES (2, 2, 2, '[y]=2', 0)",
            "INSERT INTO TablePermission VALUES (3, 1, 99, '[gone]=3', 0)")            # a table that is not in the model
        roles = {r['name']: r for r in model['roles']}
        self.assertEqual(roles['Readers']['tablePermissions'], [{'name': 'A', 'filterExpression': '[x]=1'}])
        self.assertEqual(roles['Admins']['tablePermissions'], [{'name': 'B', 'filterExpression': '[y]=2'}])
        self.assertEqual((roles['Readers']['modelPermission'], roles['Admins']['modelPermission']), ('read', 'administrator'))

    def test_calculation_group_items_belong_to_their_group(self):
        model = document(
            MODEL, table(1, 'Time', group=1), table(2, 'Other', group=2),
            "INSERT INTO CalculationGroup VALUES (1, 10)", "INSERT INTO CalculationGroup VALUES (2, 20)",
            "INSERT INTO CalculationItem VALUES (1, 1, 'YTD', 'x', 0)", "INSERT INTO CalculationItem VALUES (2, 2, 'MTD', 'y', 0)",
            "INSERT INTO CalculationItem VALUES (3, 1, 'QTD', 'z', 1)")
        groups = {t['name']: t['calculationGroup'] for t in model['tables']}
        self.assertEqual([i['name'] for i in groups['Time']['calculationItems']], ['YTD', 'QTD'])
        self.assertEqual([i['name'] for i in groups['Other']['calculationItems']], ['MTD'])


class EmptyMetadata(unittest.TestCase):
    def test_an_empty_model_table_is_a_clear_error_not_an_index_error(self):
        with self.assertRaisesRegex(ValueError, 'the Model table is empty'):
            document(table(1, 'A'))                                    # no row in Model


if __name__ == '__main__':
    unittest.main()
