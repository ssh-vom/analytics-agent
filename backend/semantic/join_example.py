"""Example demonstrating join detection and compilation."""

from semantic.types import Dataset, Column, SemanticCatalog, QuerySpec, Metric
from semantic.catalog import detect_foreign_keys, build_joins_for_query
from semantic.compiler import compile_query_spec


def main():
    """Demonstrate join functionality."""
    # Create sample datasets
    orders = Dataset(
        name="main.orders",
        schema="main",
        columns=[
            Column(name="id", data_type="INTEGER"),
            Column(name="customer_id", data_type="INTEGER"),
            Column(name="product_id", data_type="INTEGER"),
            Column(name="amount", data_type="DOUBLE", role="measure"),
            Column(name="order_date", data_type="TIMESTAMP", role="time"),
        ],
    )

    customers = Dataset(
        name="main.customers",
        schema="main",
        columns=[
            Column(name="id", data_type="INTEGER"),
            Column(name="name", data_type="VARCHAR", role="dimension"),
            Column(name="region", data_type="VARCHAR", role="dimension"),
        ],
    )

    products = Dataset(
        name="main.products",
        schema="main",
        columns=[
            Column(name="id", data_type="INTEGER"),
            Column(name="name", data_type="VARCHAR", role="dimension"),
            Column(name="category", data_type="VARCHAR", role="dimension"),
        ],
    )

    catalog = SemanticCatalog(datasets=[orders, customers, products])

    # Detect foreign keys
    print("=== Foreign Key Detection ===")
    fks = detect_foreign_keys(catalog)
    for fk in fks:
        print(f"{fk.from_dataset}.{fk.from_column} -> {fk.to_dataset}.{fk.to_column}")

    # Build joins for a multi-table query
    print("\n=== Building Joins ===")
    joins = build_joins_for_query(
        catalog,
        primary_dataset="main.orders",
        required_datasets=["main.customers", "main.products"],
    )
    for join in joins:
        print(
            f"{join.from_dataset} -> {join.to_dataset} on {join.from_column} = {join.to_column}"
        )

    # Compile a query with joins
    print("\n=== Compiled SQL ===")
    query = QuerySpec(
        dataset="main.orders",
        additional_datasets=["main.customers"],
        metrics=[Metric(column="amount", aggregation="sum")],
        dimensions=["region"],
    )

    sql = compile_query_spec(query, catalog)
    print(sql)


if __name__ == "__main__":
    main()
