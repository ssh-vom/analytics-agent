<script lang="ts">
  import { onMount } from "svelte";
  import {
    applyTheme,
    parsePersistedSettings,
    resolveThemePreference,
  } from "$lib/settingsPersistence";
  import { Moon, Sun } from "lucide-svelte";

  let theme: "dark" | "light" = "dark";

  onMount(() => {
    const saved = localStorage.getItem("textql_settings");
    if (!saved) {
      applyTheme(theme);
      return;
    }

    const parsed = parsePersistedSettings(saved);
    if (!parsed) {
      localStorage.removeItem("textql_settings");
      applyTheme(theme);
      return;
    }

    theme = resolveThemePreference(parsed.settings.theme);
    applyTheme(theme);
  });

  function toggleTheme(): void {
    theme = theme === "dark" ? "light" : "dark";
    applyTheme(theme);
    localStorage.setItem("textql_settings", JSON.stringify({ theme }));
  }
</script>

<div class="settings-page">
  <header class="page-header">
    <div class="header-content">
      <div>
        <h1>Settings</h1>
        <p class="subtitle">Choose your interface theme</p>
      </div>
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
          <h3>Theme</h3>
          <p>Switch between dark and light mode</p>
        </div>
        <div class="setting-control">
          <button
            type="button"
            class="theme-toggle"
            class:light={theme === "light"}
            on:click={toggleTheme}
            aria-label="Toggle theme"
            aria-pressed={theme === "light"}
          >
            <span class="icon-wrap dark-icon"><Moon size={14} /></span>
            <span class="toggle-track">
              <span class="toggle-thumb"></span>
            </span>
            <span class="icon-wrap light-icon"><Sun size={14} /></span>
            <span class="theme-label">{theme === "dark" ? "Dark" : "Light"}</span>
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

  .header-content { max-width: 900px; }

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

  .settings-content { padding: var(--space-6) var(--space-8); max-width: 900px; }

  .settings-section {
    background: var(--surface-0);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg);
    overflow: hidden;
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

  .setting-info h3 {
    margin: 0;
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

  .theme-toggle {
    display: inline-block;
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    background: var(--surface-1);
    color: var(--text-primary);
    padding: var(--space-2) var(--space-3);
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    min-width: 180px;
    transition: border-color var(--transition-fast), background var(--transition-fast);
  }

  .theme-toggle:hover {
    border-color: var(--border-medium);
  }

  .theme-toggle:focus-visible {
    outline: none;
    border-color: var(--accent-orange);
  }

  .icon-wrap {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: var(--text-muted);
  }

  .toggle-track {
    display: inline-flex;
  }

  .toggle-thumb {
    position: relative;
    width: 34px;
    height: 18px;
    background: var(--surface-3);
    border-radius: var(--radius-full);
    display: inline-flex;
    align-items: center;
    padding: 2px;
  }

  .toggle-thumb::before {
    content: "";
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: var(--text-secondary);
    transition: transform var(--transition-fast), background var(--transition-fast);
    transform: translateX(0);
  }

  .theme-toggle.light .toggle-thumb::before {
    transform: translateX(16px);
    background: var(--accent-orange);
  }

  .theme-label {
    font-size: 13px;
    font-weight: 500;
    min-width: 42px;
  }
</style>
