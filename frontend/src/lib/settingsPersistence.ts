export interface SettingsState {
  theme: "dark" | "light" | "auto";
  notifications: boolean;
  apiKey: string;
  defaultProvider: "openai" | "openrouter";
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

    if (parsed.defaultProvider === "openai" || parsed.defaultProvider === "openrouter") {
      next.defaultProvider = parsed.defaultProvider;
    } else if (parsed.defaultProvider === "gemini") {
      next.defaultProvider = "openrouter";
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

export function resolveThemePreference(
  theme: SettingsState["theme"] | undefined,
): "dark" | "light" {
  if (theme === "light") {
    return "light";
  }
  if (theme === "auto" && typeof window !== "undefined") {
    return window.matchMedia("(prefers-color-scheme: light)").matches
      ? "light"
      : "dark";
  }
  return "dark";
}

export function applyTheme(theme: "dark" | "light"): void {
  if (typeof document === "undefined") {
    return;
  }
  document.documentElement.setAttribute("data-theme", theme);
}
