import { describe, expect, it } from "vitest";

import { refreshWorldlineContextSnapshot } from "$lib/chat/worldlineContext";

describe("worldlineContext", () => {
  it("returns empty snapshot when worldline is missing", async () => {
    const snapshot = await refreshWorldlineContextSnapshot({
      worldlineId: "",
      selectedContextTables: ["sales"],
      connectorSelectionByWorldline: { worldline_a: ["warehouse"] },
    });

    expect(snapshot.worldlineTables).toBeNull();
    expect(snapshot.availableConnectors).toEqual([]);
    expect(snapshot.selectedConnectorIds).toEqual([]);
    expect(snapshot.selectedContextTables).toEqual([]);
  });

  it("hydrates connectors and context table selections", async () => {
    const snapshot = await refreshWorldlineContextSnapshot({
      worldlineId: "worldline_a",
      selectedContextTables: ["missing_table"],
      connectorSelectionByWorldline: {
        worldline_a: ["warehouse", "missing_alias"],
      },
      fetchTables: async () => ({
        tables: [
          {
            name: "sales",
            schema: "main",
            type: "native",
            columns: [],
          },
        ],
        count: 1,
      }),
      fetchSchema: async () => ({
        native_tables: [],
        imported_tables: [],
        attached_databases: [
          {
            alias: "warehouse",
            db_path: "/tmp/warehouse.duckdb",
            db_type: "duckdb",
            attached_at: "2026-01-01T00:00:00Z",
            tables: ["sales"],
          },
          {
            alias: "archive",
            db_path: "/tmp/archive.duckdb",
            db_type: "duckdb",
            attached_at: "2026-01-01T00:00:00Z",
            tables: ["sales_archive"],
          },
        ],
      }),
    });

    expect(snapshot.availableConnectors.map((connector) => connector.id)).toEqual([
      "warehouse",
      "archive",
    ]);
    expect(snapshot.selectedConnectorIds).toEqual(["warehouse"]);
    expect(snapshot.selectedContextTables).toEqual(["sales"]);
    expect(snapshot.connectorSelectionByWorldline.worldline_a).toEqual(["warehouse"]);
  });
});
