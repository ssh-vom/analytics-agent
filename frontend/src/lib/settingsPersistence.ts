export interface SettingsState {
  theme: "dark" | "light" | "auto";
  notifications: boolean;
  apiKey: string;
  defaultProvider: "gemini" | "openai" | "openrouter";
}

export type PersistedSettings = Omit<SettingsState, "apiKey">;

export function parsePersistedSettings(raw: string): {
  settings: Partial<PersistedSettings>;
  hadLegacyApiKey: boolean;
} | null {
  try {
    const parsed = JSON.parse(raw) as Partial<SettingsState>;
    const next: Partial<PersistedSettings> = {};

    if (parsed.theme === "dark" || parsed.theme === "light" || parsed.theme === "auto") {
      next.theme = parsed.theme;
    }

    if (typeof parsed.notifications === "boolean") {
      next.notifications = parsed.notifications;
    }

    if (
      parsed.defaultProvider === "gemini" ||
      parsed.defaultProvider === "openai" ||
      parsed.defaultProvider === "openrouter"
    ) {
      next.defaultProvider = parsed.defaultProvider;
    }

    return {
      settings: next,
      hadLegacyApiKey: typeof parsed.apiKey === "string" && parsed.apiKey.length > 0,
    };
  } catch {
    return null;
  }
}

export function buildPersistedSettings(settings: SettingsState): PersistedSettings {
  return {
    theme: settings.theme,
    notifications: settings.notifications,
    defaultProvider: settings.defaultProvider,
  };
}
