import { describe, expect, it } from "vitest";

import {
  buildPersistedSettings,
  parsePersistedSettings,
  type SettingsState,
} from "./settingsPersistence";


describe("settingsPersistence", () => {
  it("parses valid persisted settings and detects legacy api key", () => {
    const parsed = parsePersistedSettings(
      JSON.stringify({
        theme: "light",
        notifications: false,
        defaultProvider: "openrouter",
        apiKey: "legacy-secret",
      }),
    );

    expect(parsed).not.toBeNull();
    expect(parsed?.settings).toEqual({
      theme: "light",
      notifications: false,
      defaultProvider: "openrouter",
    });
    expect(parsed?.hadLegacyApiKey).toBe(true);
  });

  it("filters unsupported settings values", () => {
    const parsed = parsePersistedSettings(
      JSON.stringify({
        theme: "purple",
        notifications: "yes",
        defaultProvider: "anthropic",
      }),
    );

    expect(parsed).not.toBeNull();
    expect(parsed?.settings).toEqual({});
    expect(parsed?.hadLegacyApiKey).toBe(false);
  });

  it("returns null for malformed json", () => {
    expect(parsePersistedSettings("{bad-json}")).toBeNull();
  });

  it("builds persisted settings without api key", () => {
    const state: SettingsState = {
      theme: "dark",
      notifications: true,
      defaultProvider: "openrouter",
      apiKey: "should-not-persist",
    };

    const persisted = buildPersistedSettings(state);
    expect(persisted).toEqual({
      theme: "dark",
      notifications: true,
      defaultProvider: "openrouter",
    });
    expect("apiKey" in persisted).toBe(false);
  });

  it("migrates legacy gemini provider to openrouter", () => {
    const parsed = parsePersistedSettings(
      JSON.stringify({
        theme: "dark",
        notifications: true,
        defaultProvider: "gemini",
      }),
    );

    expect(parsed).not.toBeNull();
    expect(parsed?.settings.defaultProvider).toBe("openrouter");
  });
});
