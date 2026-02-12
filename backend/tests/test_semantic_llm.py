"""Tests for LLM-based semantic resolution."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from semantic.types import (
    SemanticCatalog,
    Dataset,
    Column,
    QuerySpec,
    Metric,
    ResolutionResult,
    Filter,
    TimeRange,
)
from semantic.prompts import format_schema_context, build_resolution_prompt
from semantic.validator import (
    validate_llm_response,
    validate_query_spec,
    calculate_confidence,
    build_resolution_result,
    ResolutionError,
)


class TestPrompts(unittest.TestCase):
    """Test prompt building functions."""

    def test_format_schema_context(self):
        """Test schema formatting for prompts."""
        dataset = Dataset(
            name="main.sales",
            schema="main",
            columns=[
                Column(name="amount", data_type="DOUBLE", role="measure"),
                Column(name="region", data_type="VARCHAR", role="dimension"),
                Column(name="date", data_type="TIMESTAMP", role="time"),
            ],
            row_count=1000,
        )
        catalog = SemanticCatalog(datasets=[dataset])

        context = format_schema_context(catalog)

        self.assertIn("main.sales", context)
        self.assertIn("amount", context)
        self.assertIn("double", context)
        self.assertIn("MEASURE", context)
        self.assertIn("1,000 rows", context)

    def test_build_resolution_prompt(self):
        """Test prompt building with schema."""
        dataset = Dataset(
            name="main.orders",
            schema="main",
            columns=[Column(name="total", data_type="DOUBLE", role="measure")],
        )
        catalog = SemanticCatalog(datasets=[dataset])

        system, user = build_resolution_prompt(
            user_query="show total sales", catalog=catalog
        )

        self.assertIn("semantic query parser", system)
        self.assertIn("main.orders", system)
        self.assertIn("total", system)
        self.assertIn("show total sales", user)


class TestValidator(unittest.TestCase):
    """Test validation functions."""

    def test_validate_llm_response_valid(self):
        """Test valid JSON parsing."""
        json_text = '{"intent": "aggregation", "confidence": 0.9}'
        result = validate_llm_response(json_text)

        self.assertEqual(result["intent"], "aggregation")
        self.assertEqual(result["confidence"], 0.9)

    def test_validate_llm_response_with_markdown(self):
        """Test JSON extraction from markdown."""
        markdown_text = '```json\n{"intent": "test"}\n```'
        result = validate_llm_response(markdown_text)

        self.assertEqual(result["intent"], "test")

    def test_validate_llm_response_invalid(self):
        """Test invalid JSON handling."""
        with self.assertRaises(ResolutionError) as ctx:
            validate_llm_response("not valid json")

        self.assertTrue(ctx.exception.retryable)

    def test_validate_llm_response_empty(self):
        """Test empty response handling."""
        with self.assertRaises(ResolutionError):
            validate_llm_response("")

    def test_validate_query_spec_success(self):
        """Test successful QuerySpec validation."""
        dataset = Dataset(
            name="main.sales",
            schema="main",
            columns=[
                Column(name="amount", data_type="DOUBLE", role="measure"),
                Column(name="region", data_type="VARCHAR", role="dimension"),
            ],
        )
        catalog = SemanticCatalog(datasets=[dataset])

        data = {
            "intent": "aggregation",
            "metrics": [{"column": "amount", "aggregation": "sum"}],
            "dimensions": ["region"],
            "confidence": 0.9,
        }

        spec = validate_query_spec(data, catalog)

        self.assertEqual(len(spec.metrics), 1)
        self.assertEqual(spec.metrics[0].column, "amount")
        self.assertEqual(spec.metrics[0].aggregation, "sum")
        self.assertEqual(spec.dimensions, ["region"])
        self.assertEqual(spec.dataset, "main.sales")

    def test_validate_query_spec_missing_column(self):
        """Test validation with non-existent column."""
        dataset = Dataset(
            name="main.sales",
            schema="main",
            columns=[Column(name="amount", data_type="DOUBLE", role="measure")],
        )
        catalog = SemanticCatalog(datasets=[dataset])

        data = {
            "metrics": [{"column": "nonexistent", "aggregation": "sum"}],
        }

        spec = validate_query_spec(data, catalog)

        # Should skip invalid metric
        self.assertEqual(len(spec.metrics), 0)

    def test_calculate_confidence_high(self):
        """Test high confidence calculation."""
        dataset = Dataset(
            name="main.sales",
            schema="main",
            columns=[Column(name="amount", data_type="DOUBLE", role="measure")],
        )
        catalog = SemanticCatalog(datasets=[dataset])

        data = {
            "intent": "aggregation",
            "confidence": 0.95,
            "metrics": [{"column": "amount", "aggregation": "sum"}],
        }

        spec = validate_query_spec(data, catalog)
        confidence = calculate_confidence(data, spec, catalog)

        self.assertGreater(confidence, 0.8)

    def test_calculate_confidence_low(self):
        """Test low confidence calculation."""
        dataset = Dataset(name="main.sales", schema="main", columns=[])
        catalog = SemanticCatalog(datasets=[dataset])

        data = {
            "intent": "unknown",
            "confidence": 0.2,
            "metrics": [],
        }

        spec = validate_query_spec(data, catalog)
        confidence = calculate_confidence(data, spec, catalog)

        self.assertLess(confidence, 0.5)

    def test_build_resolution_result(self):
        """Test building complete resolution result."""
        dataset = Dataset(
            name="main.sales",
            schema="main",
            columns=[
                Column(name="amount", data_type="DOUBLE", role="measure"),
                Column(name="region", data_type="VARCHAR", role="dimension"),
            ],
        )
        catalog = SemanticCatalog(datasets=[dataset])

        data = {
            "intent": "aggregation",
            "metrics": [{"column": "amount", "aggregation": "sum"}],
            "dimensions": ["region"],
            "confidence": 0.9,
            "ambiguous_terms": [],
            "suggestions": ["Sum of sales by region"],
        }

        result = build_resolution_result(data, catalog)

        self.assertIsInstance(result, ResolutionResult)
        self.assertIsInstance(result.query_spec, QuerySpec)
        self.assertEqual(len(result.query_spec.metrics), 1)
        self.assertGreater(result.confidence, 0.5)
        self.assertEqual(result.suggestions, ["Sum of sales by region"])


class TestLLMResolverIntegration(unittest.TestCase):
    """Integration tests for LLM resolver."""

    def test_resolution_with_valid_response(self):
        """Test end-to-end resolution with mock LLM."""
        # This would need mocking of the LLM client
        # For now, just verify the structure exists
        pass


if __name__ == "__main__":
    unittest.main()
