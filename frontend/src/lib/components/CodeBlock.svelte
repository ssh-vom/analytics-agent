<script lang="ts">
  import { onDestroy } from "svelte";
  import hljs from "highlight.js/lib/core";
  import python from "highlight.js/lib/languages/python";
  import sql from "highlight.js/lib/languages/sql";
  import { sanitizeCodeArtifacts } from "$lib/codeSanitizer";

  hljs.registerLanguage("python", python);
  hljs.registerLanguage("sql", sql);

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
      const normalized = lang.includes("python")
        ? "python"
        : lang.includes("sql")
          ? "sql"
          : "plaintext";
      if (normalized === "plaintext") {
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
    border-radius: var(--radius-md);
    border: 1px solid var(--border-soft);
    background: var(--bg-0);
    overflow: hidden;
    contain: layout style paint;
    will-change: auto;
  }

  .code-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid var(--border-soft);
    padding: 4px 10px;
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--text-dim);
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }

  pre {
    margin: 0;
    padding: var(--space-3);
    overflow-x: auto;
    overflow-y: auto;
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 13px;
    line-height: 1.5;
    min-height: 48px;
    max-height: 60vh;
    white-space: pre-wrap;
    scroll-behavior: smooth;
  }

  pre:has(code) {
    overscroll-behavior: contain;
  }

  code {
    font-family: inherit;
  }

  :global(.hljs-keyword),
  :global(.hljs-selector-tag),
  :global(.hljs-type) {
    color: #81a1c1;
  }

  :global(.hljs-title),
  :global(.hljs-title.class_),
  :global(.hljs-title.function_) {
    color: #88c0d0;
  }

  :global(.hljs-string),
  :global(.hljs-attr),
  :global(.hljs-template-tag),
  :global(.hljs-template-variable) {
    color: #a3be8c;
  }

  :global(.hljs-number),
  :global(.hljs-literal),
  :global(.hljs-symbol),
  :global(.hljs-bullet) {
    color: #b48ead;
  }

  :global(.hljs-comment),
  :global(.hljs-quote) {
    color: #616e88;
    font-style: italic;
  }

  :global(.hljs-operator),
  :global(.hljs-punctuation) {
    color: #c0c7d5;
  }

  .cursor {
    display: inline-block;
    margin-left: 1px;
    color: var(--text-dim);
    animation: blink 0.9s step-end infinite;
  }

  @keyframes blink {
    50% {
      opacity: 0;
    }
  }
</style>
