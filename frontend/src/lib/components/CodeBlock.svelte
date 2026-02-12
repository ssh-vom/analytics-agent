<script lang="ts">
  import { onDestroy } from "svelte";
  import hljs from "highlight.js/lib/core";
  import python from "highlight.js/lib/languages/python";
  import sql from "highlight.js/lib/languages/sql";
  import javascript from "highlight.js/lib/languages/javascript";
  import json from "highlight.js/lib/languages/json";
  import bash from "highlight.js/lib/languages/bash";
  import { sanitizeCodeArtifacts } from "$lib/codeSanitizer";

  hljs.registerLanguage("python", python);
  hljs.registerLanguage("sql", sql);
  hljs.registerLanguage("javascript", javascript);
  hljs.registerLanguage("typescript", javascript);
  hljs.registerLanguage("json", json);
  hljs.registerLanguage("bash", bash);
  hljs.registerLanguage("shell", bash);

  export let code = "";
  export let language = "";
  export let animate = false;
  export let placeholder = "";

  let rendered = "";
  let timer: ReturnType<typeof setInterval> | ReturnType<typeof requestAnimationFrame> | null = null;
  let lastCode = "";
  $: isRevealing = animate && rendered.length < code.length;
  // Prefer real code whenever available; only show placeholder when code is empty.
  $: content = rendered || code || placeholder;
  $: languageId = (language || "text").trim().toLowerCase();
  $: sanitizedContent = sanitizeCodeArtifacts(content);
  $: highlighted = highlightCode(sanitizedContent, languageId);

  function escapeHtml(input: string): string {
    return input
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  function highlightCode(input: string, lang: string): string {
    if (!input) {
      return "";
    }
    try {
      const langMap: Record<string, string> = {
        python: "python",
        py: "python",
        sql: "sql",
        javascript: "javascript",
        js: "javascript",
        typescript: "typescript",
        ts: "typescript",
        json: "json",
        bash: "bash",
        shell: "shell",
        sh: "bash",
      };
      const normalized = langMap[lang] ?? (lang.includes("python") ? "python" : lang.includes("sql") ? "sql" : null);
      if (!normalized) {
        return escapeHtml(input);
      }
      return hljs.highlight(input, { language: normalized }).value;
    } catch {
      return escapeHtml(input);
    }
  }

  function stopTimer(): void {
    if (timer) {
      // RAF uses a number, setInterval returns an object in some envs
      if (typeof timer === "number") {
        cancelAnimationFrame(timer);
      } else {
        clearInterval(timer);
      }
      timer = null;
    }
  }

  function startReveal(): void {
    if (!code) {
      rendered = "";
      stopTimer();
      return;
    }
    if (rendered.length >= code.length) {
      stopTimer();
      return;
    }
    if (timer) {
      return;
    }

    let lastFrameTime = performance.now();
    const frameInterval = 33; // ~30fps for smoother, less jarring animation

    function animate(currentTime: number): void {
      if (!animate || rendered.length >= code.length) {
        stopTimer();
        return;
      }

      const elapsed = currentTime - lastFrameTime;
      if (elapsed < frameInterval) {
        timer = requestAnimationFrame(animate);
        return;
      }

      lastFrameTime = currentTime;

      const remaining = code.length - rendered.length;
      const chunk = Math.max(2, Math.ceil(remaining / 8));
      rendered = code.slice(0, Math.min(code.length, rendered.length + chunk));

      if (rendered.length < code.length) {
        timer = requestAnimationFrame(animate);
      } else {
        stopTimer();
      }
    }

    timer = requestAnimationFrame(animate);
  }

  $: if (!animate) {
    stopTimer();
    rendered = code;
    lastCode = code;
  }

  $: if (animate) {
    const previousCode = lastCode;
    if (code !== previousCode) {
      const isAppendOnly =
        code.startsWith(previousCode) && rendered.startsWith(previousCode);
      lastCode = code;
      if (!isAppendOnly) {
        rendered = "";
      }
      startReveal();
    } else if (rendered.length < code.length) {
      startReveal();
    }
  }

  onDestroy(() => {
    stopTimer();
  });
</script>

<div class="code-block">
  <div class="code-header">
    <span>{language || "Code"}</span>
  </div>
  <pre><code class={`hljs lang-${languageId}`}>{@html highlighted}</code>{#if isRevealing}<span class="cursor">▋</span>{/if}</pre>
</div>

<style>
  .code-block {
    border-radius: var(--radius-lg);
    border: 1px solid var(--border-soft);
    background: #1e1e2e;
    overflow: hidden;
    contain: layout style paint;
    will-change: auto;
  }

  .code-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
    padding: 6px var(--space-4);
    font-size: 11px;
    font-family: var(--font-mono);
    color: #6c7086;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    background: rgba(0, 0, 0, 0.15);
  }

  pre {
    margin: 0;
    padding: var(--space-4) var(--space-5);
    overflow-x: auto;
    overflow-y: auto;
    color: #cdd6f4;
    font-family: var(--font-mono);
    font-size: 14px;
    line-height: 1.65;
    min-height: 48px;
    max-height: 60vh;
    white-space: pre-wrap;
    scroll-behavior: smooth;
    tab-size: 4;
  }

  pre:has(code) {
    overscroll-behavior: contain;
  }

  code {
    font-family: inherit;
  }

  /* --- Catppuccin Mocha-inspired syntax theme --- */

  :global(.hljs-keyword),
  :global(.hljs-selector-tag) {
    color: #cba6f7;
    font-weight: 500;
  }

  :global(.hljs-type),
  :global(.hljs-built_in) {
    color: #f9e2af;
  }

  :global(.hljs-title),
  :global(.hljs-title.class_),
  :global(.hljs-title.function_) {
    color: #89b4fa;
  }

  :global(.hljs-string),
  :global(.hljs-attr),
  :global(.hljs-template-tag),
  :global(.hljs-template-variable) {
    color: #a6e3a1;
  }

  :global(.hljs-number),
  :global(.hljs-literal) {
    color: #fab387;
  }

  :global(.hljs-symbol),
  :global(.hljs-bullet) {
    color: #f38ba8;
  }

  :global(.hljs-comment),
  :global(.hljs-quote) {
    color: #6c7086;
    font-style: italic;
  }

  :global(.hljs-operator) {
    color: #89dceb;
  }

  :global(.hljs-punctuation) {
    color: #9399b2;
  }

  :global(.hljs-variable),
  :global(.hljs-params) {
    color: #f2cdcd;
  }

  :global(.hljs-meta),
  :global(.hljs-meta keyword) {
    color: #f5c2e7;
  }

  :global(.hljs-regexp) {
    color: #f5c2e7;
  }

  :global(.hljs-addition) {
    color: #a6e3a1;
    background: rgba(166, 227, 161, 0.1);
  }

  :global(.hljs-deletion) {
    color: #f38ba8;
    background: rgba(243, 139, 168, 0.1);
  }

  .cursor {
    display: inline-block;
    margin-left: 1px;
    color: #cba6f7;
    animation: blink 0.9s step-end infinite;
  }

  @keyframes blink {
    50% {
      opacity: 0;
    }
  }
</style>
