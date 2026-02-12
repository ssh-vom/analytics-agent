#!/usr/bin/env python3
from __future__ import annotations

import argparse
import random
from datetime import date, timedelta
from pathlib import Path

import duckdb

REGIONS = ("na", "emea", "apac")
SEGMENTS = ("enterprise", "mid_market", "smb")
PRODUCTS = ("alpha", "beta", "gamma")


def build_finance_rows(*, start_date: date, days: int, seed: int) -> list[tuple]:
    rng = random.Random(seed)
    rows: list[tuple] = []

    for day_offset in range(days):
        trading_date = start_date + timedelta(days=day_offset)
        seasonality = 1.0 + ((trading_date.month - 6) * 0.015)

        for region_index, region in enumerate(REGIONS):
            region_multiplier = 1.0 + (region_index * 0.07)
            for segment_index, segment in enumerate(SEGMENTS):
                segment_multiplier = 1.0 + (segment_index * 0.05)
                for product_index, product in enumerate(PRODUCTS):
                    base_customers = 70 + product_index * 18 + rng.randint(-14, 20)
                    customers = max(12, int(base_customers * segment_multiplier))

                    unit_price = 95 + product_index * 16 + rng.uniform(-6.5, 6.5)
                    revenue = round(
                        customers * unit_price * seasonality * region_multiplier,
                        2,
                    )
                    cogs = round(revenue * (0.53 + rng.uniform(-0.04, 0.05)), 2)
                    opex = round(revenue * (0.18 + rng.uniform(-0.02, 0.03)), 2)
                    refunds = round(revenue * (0.01 + rng.uniform(0.00, 0.015)), 2)
                    marketing_spend = round(
                        revenue * (0.08 + rng.uniform(-0.02, 0.02)),
                        2,
                    )

                    rows.append(
                        (
                            trading_date.isoformat(),
                            region,
                            segment,
                            product,
                            customers,
                            revenue,
                            cogs,
                            opex,
                            refunds,
                            marketing_spend,
                        )
                    )

    return rows


def generate_demo_db(
    output_path: Path, *, days: int, seed: int, overwrite: bool
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        if not overwrite:
            raise SystemExit(
                f"Refusing to overwrite existing file: {output_path} (use --overwrite)"
            )
        output_path.unlink()

    rows = build_finance_rows(start_date=date(2025, 1, 1), days=days, seed=seed)

    conn = duckdb.connect(str(output_path))
    try:
        conn.execute(
            """
            CREATE TABLE finance_daily (
                trading_date DATE NOT NULL,
                region VARCHAR NOT NULL,
                segment VARCHAR NOT NULL,
                product VARCHAR NOT NULL,
                customers INTEGER NOT NULL,
                revenue DOUBLE NOT NULL,
                cogs DOUBLE NOT NULL,
                opex DOUBLE NOT NULL,
                refunds DOUBLE NOT NULL,
                marketing_spend DOUBLE NOT NULL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO finance_daily (
                trading_date,
                region,
                segment,
                product,
                customers,
                revenue,
                cogs,
                opex,
                refunds,
                marketing_spend
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.execute(
            """
            CREATE VIEW finance_metrics AS
            SELECT
                trading_date,
                region,
                segment,
                product,
                customers,
                revenue,
                cogs,
                opex,
                refunds,
                marketing_spend,
                ROUND(revenue - cogs - opex - refunds - marketing_spend, 2) AS gross_profit
            FROM finance_daily
            """
        )
    finally:
        conn.close()

    return len(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate deterministic finance demo DuckDB file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("backend/data/demo/finance_demo.duckdb"),
        help="Output DuckDB path",
    )
    parser.add_argument(
        "--days", type=int, default=120, help="Number of days to generate"
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite output file if it exists",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    row_count = generate_demo_db(
        args.output,
        days=args.days,
        seed=args.seed,
        overwrite=args.overwrite,
    )
    print(f"Generated {row_count} rows at {args.output}")


if __name__ == "__main__":
    main()
