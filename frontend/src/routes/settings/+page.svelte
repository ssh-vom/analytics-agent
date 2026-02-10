<script lang="ts">
  import { onMount } from "svelte";
  import { Settings as SettingsIcon } from "lucide-svelte";
  import { Moon } from "lucide-svelte";
  import { Bell } from "lucide-svelte";
  import { Key } from "lucide-svelte";
  import { Save } from "lucide-svelte";

  interface SettingsState {
    theme: "dark" | "light" | "auto";
    notifications: boolean;
    apiKey: string;
    defaultProvider: string;
  }

  type PersistedSettings = Omit<SettingsState, "apiKey">;

  let settings: SettingsState = {
    theme: "dark",
    notifications: true,
    apiKey: "",
    defaultProvider: "gemini",
  };

  let hasChanges = false;

  function parsePersistedSettings(raw: string): {
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

  onMount(() => {
    const saved = localStorage.getItem("textql_settings");
    if (!saved) {
      return;
    }

    const parsed = parsePersistedSettings(saved);
    if (!parsed) {
      localStorage.removeItem("textql_settings");
      return;
    }

    settings = { ...settings, ...parsed.settings, apiKey: "" };
    if (parsed.hadLegacyApiKey) {
      localStorage.setItem("textql_settings", JSON.stringify(parsed.settings));
    }
  });

  function saveSettings() {
    const persisted: PersistedSettings = {
      theme: settings.theme,
      notifications: settings.notifications,
      defaultProvider: settings.defaultProvider,
    };
    localStorage.setItem("textql_settings", JSON.stringify(persisted));
    hasChanges = false;
  }

  function handleChange() {
    hasChanges = true;
  }
</script>

<div class="settings-page">
  <header class="page-header">
    <div class="header-content">
      <div>
        <h1>Settings</h1>
        <p class="subtitle">Configure your AnalyticZ preferences</p>
      </div>
      {#if hasChanges}
        <button class="save-btn" on:click={saveSettings}>
          <Save size={18} />
          <span>Save Changes</span>
        </button>
      {/if}
    </div>
  </header>

  <main class="settings-content">
    <section class="settings-section">
      <div class="section-header">
        <Moon size={20} />
        <h2>Appearance</h2>
      </div>
      
      <div class="setting-item">
        <div class="setting-info">
          <label>Theme</label>
          <p>Choose your preferred color scheme</p>
        </div>
        <div class="setting-control">
          <select bind:value={settings.theme} on:change={handleChange}>
            <option value="dark">Dark</option>
            <option value="light">Light</option>
            <option value="auto">Auto</option>
          </select>
        </div>
      </div>
    </section>

    <section class="settings-section">
      <div class="section-header">
        <Key size={20} />
        <h2>API Configuration</h2>
      </div>
      
      <div class="setting-item">
        <div class="setting-info">
          <label>Default Provider</label>
          <p>Select your preferred AI provider</p>
        </div>
        <div class="setting-control">
          <select bind:value={settings.defaultProvider} on:change={handleChange}>
            <option value="gemini">Gemini</option>
            <option value="openai">OpenAI</option>
            <option value="openrouter">OpenRouter</option>
          </select>
        </div>
      </div>

      <div class="setting-item">
        <div class="setting-info">
          <label>API Key</label>
          <p>Your API key is used for this session only and is never saved to local storage</p>
        </div>
        <div class="setting-control">
          <input 
            type="password" 
            bind:value={settings.apiKey}
            on:input={handleChange}
            placeholder="Enter your API key"
          />
        </div>
      </div>
    </section>

    <section class="settings-section">
      <div class="section-header">
        <Bell size={20} />
        <h2>Notifications</h2>
      </div>
      
      <div class="setting-item">
        <div class="setting-info">
          <label>Enable Notifications</label>
          <p>Receive alerts for completed queries and errors</p>
        </div>
        <div class="setting-control">
          <label class="toggle">
            <input 
              type="checkbox" 
              bind:checked={settings.notifications}
              on:change={handleChange}
            />
            <span class="toggle-slider"></span>
          </label>
        </div>
      </div>
    </section>

    <section class="settings-section danger">
      <div class="section-header">
        <SettingsIcon size={20} />
        <h2>Data Management</h2>
      </div>
      
      <div class="setting-item">
        <div class="setting-info">
          <label>Clear Local Data</label>
          <p>Remove all threads, connectors, and settings from local storage</p>
        </div>
        <div class="setting-control">
          <button 
            class="danger-btn"
            on:click={() => {
              if (confirm("Are you sure? This will delete all local data.")) {
                localStorage.clear();
                location.reload();
              }
            }}
          >
            Clear Data
          </button>
        </div>
      </div>
    </section>
  </main>
</div>

<style>
  .settings-page {
    height: 100vh;
    overflow-y: auto;
    background: var(--bg-0);
  }

  .page-header {
    padding: var(--space-6) var(--space-8);
    border-bottom: 1px solid var(--border-soft);
    background: var(--surface-0);
  }

  .header-content {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--space-4);
    max-width: 900px;
  }

  h1 {
    margin: 0 0 var(--space-2);
    font-family: var(--font-heading);
    font-size: 28px;
    font-weight: 500;
    color: var(--text-primary);
  }

  .subtitle {
    margin: 0;
    color: var(--text-muted);
    font-size: 15px;
  }

  .save-btn {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-4);
    background: var(--accent-cyan);
    border: none;
    border-radius: var(--radius-md);
    color: #111;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .save-btn:hover {
    background: var(--accent-cyan);
    filter: brightness(1.1);
    transform: translateY(-1px);
  }

  .settings-content {
    padding: var(--space-6) var(--space-8);
    max-width: 900px;
    display: flex;
    flex-direction: column;
    gap: var(--space-6);
  }

  .settings-section {
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
    overflow: hidden;
  }

  .settings-section.danger {
    border-color: var(--danger-muted);
  }

  .section-header {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-4) var(--space-5);
    background: var(--surface-1);
    border-bottom: 1px solid var(--border-soft);
    color: var(--text-secondary);
  }

  .section-header h2 {
    margin: 0;
    font-size: 16px;
    font-weight: 600;
    color: var(--text-primary);
  }

  .setting-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-4);
    padding: var(--space-4) var(--space-5);
    border-bottom: 1px solid var(--border-soft);
  }

  .setting-item:last-child {
    border-bottom: none;
  }

  .setting-info {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }

  .setting-info label {
    font-size: 14px;
    font-weight: 500;
    color: var(--text-primary);
  }

  .setting-info p {
    margin: 0;
    font-size: 13px;
    color: var(--text-dim);
  }

  .setting-control {
    display: flex;
    align-items: center;
  }

  .setting-control select,
  .setting-control input {
    padding: var(--space-2) var(--space-3);
    background: var(--surface-1);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    color: var(--text-primary);
    font-size: 14px;
    min-width: 200px;
    transition: all var(--transition-fast);
  }

  .setting-control select:focus,
  .setting-control input:focus {
    outline: none;
    border-color: var(--accent-orange);
  }

  /* Toggle Switch */
  .toggle {
    position: relative;
    display: inline-block;
    width: 44px;
    height: 24px;
  }

  .toggle input {
    opacity: 0;
    width: 0;
    height: 0;
  }

  .toggle-slider {
    position: absolute;
    cursor: pointer;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background-color: var(--surface-2);
    transition: var(--transition-fast);
    border-radius: var(--radius-full);
  }

  .toggle-slider:before {
    position: absolute;
    content: "";
    height: 18px;
    width: 18px;
    left: 3px;
    bottom: 3px;
    background-color: var(--text-secondary);
    transition: var(--transition-fast);
    border-radius: 50%;
  }

  .toggle input:checked + .toggle-slider {
    background-color: var(--accent-orange-muted);
  }

  .toggle input:checked + .toggle-slider:before {
    transform: translateX(20px);
    background-color: var(--accent-orange);
  }

  .danger-btn {
    padding: var(--space-2) var(--space-4);
    background: transparent;
    border: 1px solid var(--danger);
    border-radius: var(--radius-md);
    color: var(--danger);
    font-size: 14px;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .danger-btn:hover {
    background: var(--danger-muted);
  }
</style>
