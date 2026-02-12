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


class TestJoinDetection(unittest.TestCase):
    """Test foreign key detection and join path finding."""

    def test_detect_foreign_keys_by_convention(self):
        """Test FK detection using naming conventions."""
        from semantic.catalog import detect_foreign_keys
        from semantic.types import Dataset, Column

        # Create mock datasets
        orders_dataset = Dataset(
            name="main.orders",
            schema="main",
            columns=[
                Column(name="id", data_type="INTEGER"),
                Column(name="customer_id", data_type="INTEGER"),  # FK to customers
                Column(name="amount", data_type="DOUBLE"),
            ],
        )

        customers_dataset = Dataset(
            name="main.customers",
            schema="main",
            columns=[
                Column(name="id", data_type="INTEGER"),  # Primary key
                Column(name="name", data_type="VARCHAR"),
            ],
        )

        catalog = SemanticCatalog(datasets=[orders_dataset, customers_dataset])
        fks = detect_foreign_keys(catalog)

        self.assertEqual(len(fks), 1)
        self.assertEqual(fks[0].from_dataset, "main.orders")
        self.assertEqual(fks[0].from_column, "customer_id")
        self.assertEqual(fks[0].to_dataset, "main.customers")
        self.assertEqual(fks[0].to_column, "id")

    def test_build_joins_for_query(self):
        """Test automatic join building."""
        from semantic.catalog import build_joins_for_query
        from semantic.types import Dataset, Column

        orders = Dataset(
            name="main.orders",
            schema="main",
            columns=[
                Column(name="id", data_type="INTEGER"),
                Column(name="customer_id", data_type="INTEGER"),
                Column(name="amount", data_type="DOUBLE"),
            ],
        )

        customers = Dataset(
            name="main.customers",
            schema="main",
            columns=[
                Column(name="id", data_type="INTEGER"),
                Column(name="name", data_type="VARCHAR"),
            ],
        )

        catalog = SemanticCatalog(datasets=[orders, customers])
        joins = build_joins_for_query(
            catalog,
            primary_dataset="main.orders",
            required_datasets=["main.customers"],
        )

        self.assertEqual(len(joins), 1)
        self.assertEqual(joins[0].from_dataset, "main.orders")
        self.assertEqual(joins[0].to_dataset, "main.customers")


class TestJoinCompilation(unittest.TestCase):
    """Test SQL compilation with JOINs."""

    def test_compile_join_clause(self):
        """Test JOIN clause compilation."""
        from semantic.compiler import compile_join
        from semantic.types import Join

        join = Join(
            from_dataset="main.orders",
            to_dataset="main.customers",
            from_column="customer_id",
            to_column="id",
            join_type="left",
        )

        sql = compile_join(join)
        self.assertIn("LEFT JOIN", sql)
        self.assertIn("main.customers", sql)
        self.assertIn("customer_id", sql)
        self.assertIn("ON", sql)

    def test_compile_query_with_joins(self):
        """Test full query compilation with joins."""
        from semantic.types import Dataset, Column, QuerySpec, Metric
        from semantic.compiler import compile_query_spec

        orders = Dataset(
            name="main.orders",
            schema="main",
            columns=[
                Column(name="id", data_type="INTEGER"),
                Column(name="customer_id", data_type="INTEGER"),
                Column(name="amount", data_type="DOUBLE", role="measure"),
            ],
        )

        customers = Dataset(
            name="main.customers",
            schema="main",
            columns=[
                Column(name="id", data_type="INTEGER"),
                Column(name="name", data_type="VARCHAR", role="dimension"),
            ],
        )

        catalog = SemanticCatalog(datasets=[orders, customers])

        query = QuerySpec(
            dataset="main.orders",
            additional_datasets=["main.customers"],
            metrics=[Metric(column="amount", aggregation="sum")],
            dimensions=["name"],
        )

        sql = compile_query_spec(query, catalog)

        self.assertIsNotNone(sql)
        self.assertIn("JOIN", sql)
        self.assertIn("main.customers", sql)


if __name__ == "__main__":
    unittest.main()
