export type Provider = "gemini" | "openai" | "openrouter";
export type OutputType = "report" | "dashboard";

export interface ContextSettings {
  webSearch: boolean;
  dashboards: boolean;
  textToSql: boolean;
  ontology: boolean;
}

export interface StoredConnector {
  id: string;
  name: string;
  isActive: boolean;
}

export function isStoredConnectorList(value: unknown): value is StoredConnector[] {
  return (
    Array.isArray(value) &&
    value.every(
      (item) =>
        typeof item === "object" &&
        item !== null &&
        typeof (item as { id?: unknown }).id === "string" &&
        typeof (item as { name?: unknown }).name === "string" &&
        typeof (item as { isActive?: unknown }).isActive === "boolean",
    )
  );
}

export function activeConnectorIds(connectors: StoredConnector[]): string[] {
  return connectors.filter((connector) => connector.isActive).map((connector) => connector.id);
}

export function toggleSelectedId(values: string[], id: string): string[] {
  if (values.includes(id)) {
    return values.filter((value) => value !== id);
  }
  return [...values, id];
}

export function buildContextualMessage(
  message: string,
  options: {
    outputType: OutputType;
    availableConnectors: StoredConnector[];
    selectedConnectorIds: string[];
    selectedContextTables: string[];
    contextSettings: ContextSettings;
  },
): string {
  const selectedConnectors = options.availableConnectors
    .filter((connector) => options.selectedConnectorIds.includes(connector.id))
    .map((connector) => connector.name);

  const contextLines: string[] = [`output_type=${options.outputType}`];

  if (options.selectedContextTables.length > 0) {
    contextLines.push(`tables=${options.selectedContextTables.join(",")}`);
  }
  if (options.availableConnectors.length > 0) {
    if (selectedConnectors.length > 0) {
      contextLines.push(`connectors=${selectedConnectors.join(",")}`);
    } else {
      contextLines.push("connectors=none");
    }
  }

  const enabledSettings = Object.entries(options.contextSettings)
    .filter(([, enabled]) => enabled)
    .map(([key]) => key);
  if (enabledSettings.length > 0) {
    contextLines.push(`settings=${enabledSettings.join(",")}`);
  }

  if (contextLines.length === 0) {
    return message;
  }

  return `${message}\n\n<context>\n${contextLines
    .map((line) => `- ${line}`)
    .join("\n")}\n</context>`;
}

export function providerLabel(provider: Provider): string {
  switch (provider) {
    case "gemini":
      return "Gemini";
    case "openai":
      return "OpenAI";
    case "openrouter":
      return "OpenRouter";
  }
}
