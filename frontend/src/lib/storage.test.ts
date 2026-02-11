import { describe, expect, it } from "vitest";

import { safeJsonParse } from "./storage";


describe("safeJsonParse", () => {
  it("parses valid json", () => {
    const parsed = safeJsonParse<{ id: string }>("{\"id\":\"x\"}");
    expect(parsed).toEqual({ id: "x" });
  });

  it("returns null for malformed json", () => {
    expect(safeJsonParse("{bad-json}")).toBeNull();
  });

  it("returns null when validator fails", () => {
    const parsed = safeJsonParse<{ id: string }>(
      "{\"name\":\"x\"}",
      (value): value is { id: string } =>
        typeof value === "object" && value !== null && typeof (value as { id?: unknown }).id === "string",
    );
    expect(parsed).toBeNull();
  });
});
