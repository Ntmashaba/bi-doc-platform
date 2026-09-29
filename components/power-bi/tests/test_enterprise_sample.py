"""Teradata, Oracle, ODBC, Databricks, dataflow and DirectQuery via the synthetic
Network Operations sample (tests/samples/build_enterprise_sample.py)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "samples"))
from build_enterprise_sample import build_enterprise_payload, build_model  # noqa: E402


class EnterpriseSampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = build_model()
        cls.payload = build_enterprise_payload(cls.model)
        cls.sources = {t["name"]: t["partitions"][0]["source"] for t in cls.model["tables"]}

    def identity(self, table):
        s = self.sources[table]
        return s["sourceType"], s.get("server"), s.get("database"), s.get("schema"), s.get("object")

    def test_teradata_navigation_through_a_shared_query(self):
        self.assertEqual(self.identity("Network Usage"), ("Teradata", "tdprod.corp.example", "EDW_PROD", None, "USAGE_DAILY"))
        self.assertEqual(self.model["tables"][0]["partitions"][0]["mode"], "directQuery")

    def test_teradata_native_sql_leads_with_its_from_table(self):
        source = self.sources["Subscriber"]
        self.assertEqual(self.identity("Subscriber"), ("Teradata", "tdprod.corp.example", "EDW_PROD", None, "SUBSCRIBER"))
        self.assertTrue(source["nativeQuery"])
        self.assertIn("Teradata · tdprod.corp.example / EDW_PROD", " ".join(source.get("otherSources", [])) + source["label"])

    def test_teradata_over_odbc_dsn_is_partial(self):
        self.assertEqual(self.identity("Cell Site"), ("ODBC", "dsn=TD_PROD", None, "EDW_PROD", "CELL_SITE"))
        self.assertEqual(self.sources["Cell Site"]["traceStatus"], "Partial")

    def test_oracle_service_parameter_and_native_query(self):
        for table, obj in (("Invoice", "INVOICE"), ("Payment", "PAYMENT")):
            self.assertEqual(self.identity(table), ("Oracle", "ora-bill.corp.example:1521/BILLPRD", "BILLPRD", "BILLING", obj))

    def test_oracle_tns_alias_is_resolved_and_names_folded(self):
        self.assertEqual(self.identity("Tariff"), ("Oracle", "BILLPRD", None, "BILLING", "TARIFF_PLAN"))
        self.assertEqual(self.sources["Tariff"]["traceStatus"], "Resolved")

    def test_databricks_and_dataflow(self):
        self.assertEqual(self.identity("Cell KPI"), ("Databricks", "adb-1234567890.12.azuredatabricks.net", "main",
                                                     "network", "cell_kpi_daily"))
        self.assertEqual(self.sources["Region Target"]["sourceType"], "Power Platform dataflow")
        self.assertEqual(self.sources["Region Target"]["object"], "RegionTargets")

    def test_every_external_input_is_identified(self):
        rows = self.payload["primarySources"]["rows"]
        self.assertFalse(self.payload["primarySources"]["unresolved"])
        self.assertEqual({r["sourceType"] for r in rows},
                         {"Teradata", "Oracle", "ODBC", "Databricks", "Power Platform dataflow"})


if __name__ == "__main__":
    unittest.main()
