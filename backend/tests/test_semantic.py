"""Basic tests for semantic layer."""

import unittest
from semantic.types import Dataset, Column, QuerySpec, Metric, SemanticCatalog
from semantic.compiler import compile_query_spec


class TestSemanticTypes(unittest.TestCase):
    def test_dataset_creation(self):
        col = Column(name="sales", data_type="DOUBLE", role="measure")
        self.assertEqual(col.name, "sales")
        self.assertEqual(col.role, "measure")

    def test_catalog_get_dataset(self):
        ds = Dataset(name="main.revenue", schema="main", columns=[], row_count=100)
        catalog = SemanticCatalog(datasets=[ds])

        found = catalog.get_dataset("main.revenue")
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "main.revenue")

        not_found = catalog.get_dataset("other.table")
        self.assertIsNone(not_found)


class TestCompiler(unittest.TestCase):
    def test_compile_simple_query(self):
        # Setup
        column = Column(name="amount", data_type="DOUBLE", role="measure")
        dataset = Dataset(
            name="main.sales", schema="main", columns=[column], row_count=1000
        )
        catalog = SemanticCatalog(datasets=[dataset])

        query = QuerySpec(
            dataset="main.sales",
            metrics=[Metric(column="amount", aggregation="sum", alias="total_amount")],
            limit=100,
        )

        # Compile
        sql = compile_query_spec(query, catalog)

        # Verify
        self.assertIsNotNone(sql)
        self.assertIn("SELECT", sql)
        self.assertIn('SUM("amount")', sql)
        self.assertIn("FROM main.sales", sql)
        self.assertIn("LIMIT 100", sql)

    def test_compile_with_dimensions(self):
        # Setup
        region_col = Column(name="region", data_type="VARCHAR", role="dimension")
        amount_col = Column(name="amount", data_type="DOUBLE", role="measure")
        dataset = Dataset(
            name="main.sales",
            schema="main",
            columns=[region_col, amount_col],
            row_count=1000,
        )
        catalog = SemanticCatalog(datasets=[dataset])

        query = QuerySpec(
            dataset="main.sales",
            metrics=[Metric(column="amount", aggregation="sum")],
            dimensions=["region"],
            limit=50,
        )

        # Compile
        sql = compile_query_spec(query, catalog)

        # Verify
        self.assertIsNotNone(sql)
        self.assertIn('"region"', sql)
        self.assertIn("GROUP BY", sql)


class TestCatalogIntrospection(unittest.TestCase):
    def test_infer_column_role(self):
        from semantic.catalog import infer_column_role

        # Numeric should be measure
        self.assertEqual(infer_column_role("sales", "DOUBLE"), "measure")

        # ID columns should be dimension
        self.assertEqual(infer_column_role("customer_id", "INTEGER"), "dimension")

        # Timestamp should be time
        self.assertEqual(infer_column_role("created_at", "TIMESTAMP"), "time")

        # Text should be dimension
        self.assertEqual(infer_column_role("name", "VARCHAR"), "dimension")


if __name__ == "__main__":
    unittest.main()
