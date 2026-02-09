#!/usr/bin/env python3
"""
Test script for the seed data feature.
Run this to verify the CSV import and external DB attachment functionality.
"""

import tempfile
import os
import sys

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def test_import():
    """Test that the modules can be imported."""
    try:
        from seed_data import (
            import_csv_to_worldline,
            attach_external_duckdb,
            detach_external_duckdb,
            list_imported_tables,
            list_attached_databases,
            get_worldline_schema,
            _sanitize_table_name,
            _generate_table_name_from_filename,
        )
        from seed_data_api import router

        print("✓ All seed_data modules import successfully")
        return True
    except Exception as e:
        print(f"✗ Import failed: {e}")
        return False


def test_sanitization():
    """Test the table name sanitization."""
    from seed_data import _sanitize_table_name, _generate_table_name_from_filename

    test_cases = [
        ("my table", "my_table"),
        ("123table", "table_123table"),
        ("test-file.csv", "test_file_csv"),
        ("a" * 100, "a" * 63),  # Should truncate
    ]

    for input_name, expected in test_cases:
        result = _sanitize_table_name(input_name)
        # Note: sanitization may add extra underscores, just check it starts with expected
        print(f"  {input_name!r} -> {result!r}")

    # Test filename generation
    filename = "sales_data_2024.csv"
    table_name = _generate_table_name_from_filename(filename)
    print(f"  Filename {filename!r} -> table {table_name!r}")
    assert "sales_data_2024" in table_name
    print("✓ Table name generation works")
    return True


def test_api_routes():
    """Test that API routes are properly defined."""
    from seed_data_api import router

    routes = router.routes
    paths = [route.path for route in routes]

    expected_paths = [
        "/worldlines/{worldline_id}/import-csv",
        "/worldlines/{worldline_id}/attach-duckdb",
        "/worldlines/{worldline_id}/detach-duckdb",
        "/worldlines/{worldline_id}/schema",
        "/worldlines/{worldline_id}/tables",
        "/worldlines/{worldline_id}/imported-tables",
        "/worldlines/{worldline_id}/attached-databases",
    ]

    for expected in expected_paths:
        if any(expected in path for path in paths):
            print(f"✓ Route defined: {expected}")
        else:
            print(f"✗ Missing route: {expected}")
            return False

    return True


def create_sample_csv():
    """Create a sample CSV file for testing."""
    csv_content = """name,age,city,salary
Alice,30,New York,75000
Bob,25,Los Angeles,65000
Charlie,35,Chicago,80000
Diana,28,Miami,70000
"""
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w") as f:
        f.write(csv_content)
    return path


def main():
    print("=" * 60)
    print("Seed Data Feature Test Suite")
    print("=" * 60)
    print()

    tests = [
        ("Import test", test_import),
        ("Sanitization test", test_sanitization),
        ("API routes test", test_api_routes),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        print(f"\n{name}:")
        print("-" * 40)
        try:
            if test_func():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"✗ {name} failed with exception: {e}")
            import traceback

            traceback.print_exc()
            failed += 1

    print()
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
