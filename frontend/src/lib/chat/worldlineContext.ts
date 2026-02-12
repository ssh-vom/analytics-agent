import {
  fetchWorldlineSchema,
  fetchWorldlineTables,
} from "$lib/api/client";
import type { StoredConnector } from "$lib/chat/contextControls";

type WorldlineTablesResult = Awaited<ReturnType<typeof fetchWorldlineTables>>;

export type WorldlineContextSnapshot = {
  worldlineTables: WorldlineTablesResult | null;
  availableConnectors: StoredConnector[];
  selectedConnectorIds: string[];
  connectorSelectionByWorldline: Record<string, string[]>;
  selectedContextTables: string[];
};

function emptyContextSnapshot(
  connectorSelectionByWorldline: Record<string, string[]>,
): WorldlineContextSnapshot {
  return {
    worldlineTables: null,
    availableConnectors: [],
    selectedConnectorIds: [],
    connectorSelectionByWorldline,
    selectedContextTables: [],
  };
}

export async function refreshWorldlineContextSnapshot(options: {
  worldlineId: string;
  selectedContextTables: string[];
  connectorSelectionByWorldline: Record<string, string[]>;
  fetchTables?: typeof fetchWorldlineTables;
  fetchSchema?: typeof fetchWorldlineSchema;
}): Promise<WorldlineContextSnapshot> {
  const {
    worldlineId,
    selectedContextTables,
    connectorSelectionByWorldline,
    fetchTables = fetchWorldlineTables,
    fetchSchema = fetchWorldlineSchema,
  } = options;

  if (!worldlineId) {
    return emptyContextSnapshot(connectorSelectionByWorldline);
  }

  try {
    const [tables, schema] = await Promise.all([
      fetchTables(worldlineId),
      fetchSchema(worldlineId),
    ]);

    const availableConnectors: StoredConnector[] = schema.attached_databases.map(
      (database) => ({
        id: database.alias,
        name: database.alias,
        isActive: true,
      }),
    );

    const previousSelection = connectorSelectionByWorldline[worldlineId];
    const selectedConnectorIds = previousSelection
      ? previousSelection.filter((alias) =>
          availableConnectors.some((connector) => connector.id === alias),
        )
      : availableConnectors.map((connector) => connector.id);

    const nextConnectorSelection = {
      ...connectorSelectionByWorldline,
      [worldlineId]: selectedConnectorIds,
    };

    const nextSelectedContextTables = selectedContextTables.filter((selected) =>
      tables.tables.some((table) => table.name === selected),
    );
    if (nextSelectedContextTables.length === 0 && tables.tables.length > 0) {
      nextSelectedContextTables.push(tables.tables[0].name);
    }

    return {
      worldlineTables: tables,
      availableConnectors,
      selectedConnectorIds,
      connectorSelectionByWorldline: nextConnectorSelection,
      selectedContextTables: nextSelectedContextTables,
    };
  } catch {
    return emptyContextSnapshot(connectorSelectionByWorldline);
  }
}
