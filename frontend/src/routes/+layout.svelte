<script lang="ts">
    import { onMount } from "svelte";
    import "../styles/tokens.css";
    import "../styles/base.css";
    import "../styles/animations.css";
    import AppLayout from "$lib/components/layout/AppLayout.svelte";
    import {
        applyTheme,
        parsePersistedSettings,
        resolveThemePreference,
    } from "$lib/settingsPersistence";

    onMount(() => {
        const raw = localStorage.getItem("textql_settings");
        const parsed = raw ? parsePersistedSettings(raw) : null;
        applyTheme(resolveThemePreference(parsed?.settings.theme));
    });
</script>

<AppLayout>
    <slot />
</AppLayout>
