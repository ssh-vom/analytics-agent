import { describe, expect, it } from "vitest";

import {
  buildContextualMessage,
  isStoredConnectorList,
  providerLabel,
  toggleSelectedId,
  type ContextSettings,
  type StoredConnector,
} from "./contextControls";


describe("contextControls", () => {
  it("toggles selected ids", () => {
    expect(toggleSelectedId(["a", "b"], "b")).toEqual(["a"]);
    expect(toggleSelectedId(["a"], "c")).toEqual(["a", "c"]);
  });

  it("builds contextual message with selected options", () => {
    const connectors: StoredConnector[] = [
      { id: "c1", name: "warehouse", isActive: true },
      { id: "c2", name: "archive", isActive: false },
    ];
    const settings: ContextSettings = {
      webSearch: true,
      dashboards: false,
      textToSql: true,
      ontology: false,
    };

    const message = buildContextualMessage("analyze churn", {
      outputType: "dashboard",
      availableConnectors: connectors,
      selectedConnectorIds: ["c1"],
      selectedContextTables: ["customers", "subscriptions"],
      contextSettings: settings,
    });

    expect(message).toContain("analyze churn");
    expect(message).toContain("output_type=dashboard");
    expect(message).toContain("tables=customers,subscriptions");
    expect(message).toContain("connectors=warehouse");
    expect(message).toContain("settings=webSearch,textToSql");
  });

  it("validates stored connectors", () => {
    expect(
      isStoredConnectorList([{ id: "c1", name: "warehouse", isActive: true }]),
    ).toBe(true);
    expect(isStoredConnectorList([{ id: "c1", name: "warehouse" }])).toBe(false);
  });

  it("maps provider labels", () => {
    expect(providerLabel("gemini")).toBe("Gemini");
    expect(providerLabel("openai")).toBe("OpenAI");
    expect(providerLabel("openrouter")).toBe("OpenRouter");
  });
});
