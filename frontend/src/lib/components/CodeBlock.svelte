<script lang="ts">
  import { onDestroy } from "svelte";

  export let code = "";
  export let language = "";
  export let animate = false;
  export let placeholder = "";

  let rendered = "";
  let timer: ReturnType<typeof setInterval> | null = null;
  let lastCode = "";
  let animatedForCurrentCode = false;
  $: isRevealing = animate && rendered.length < code.length;
  $: content = rendered || placeholder;
  $: languageId = (language || "text").trim().toLowerCase();
  $: highlighted = highlightCode(content, languageId);

  function escapeHtml(input: string): string {
    return input
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  function wrapToken(input: string, tokenClass: string): string {
    return `<span class="token ${tokenClass}">${escapeHtml(input)}</span>`;
  }

  function protectSegments(
    input: string,
    patterns: Array<{ regex: RegExp; tokenClass: string }>,
  ): { text: string; tokens: string[] } {
    const tokens: string[] = [];
    let text = input;
    for (const pattern of patterns) {
      text = text.replace(pattern.regex, (match) => {
        const marker = `\u0000TOK${tokens.length}\u0000`;
        tokens.push(wrapToken(match, pattern.tokenClass));
        return marker;
      });
    }
    return { text, tokens };
  }

  function restoreSegments(input: string, tokens: string[]): string {
    return input.replace(/\u0000TOK(\d+)\u0000/g, (_, idx) => {
      const token = tokens[Number(idx)];
      return token ?? "";
    });
  }

  function highlightWithKeywords(
    input: string,
    keywords: string[],
    extra: Array<{ regex: RegExp; tokenClass: string }>,
  ): string {
    let html = escapeHtml(input);
    for (const item of extra) {
      html = html.replace(item.regex, (match) => {
        return `<span class="token ${item.tokenClass}">${match}</span>`;
      });
    }

    if (keywords.length) {
      const keywordPattern = new RegExp(`\\b(${keywords.join("|")})\\b`, "gi");
      html = html.replace(keywordPattern, (match) => {
        return `<span class="token keyword">${match}</span>`;
      });
    }
    return html;
  }

  function highlightSql(input: string): string {
    const protectedSql = protectSegments(input, [
      { regex: /--.*$/gm, tokenClass: "comment" },
      { regex: /'(?:''|[^'])*'/g, tokenClass: "string" },
    ]);

    const keywords = [
      "select",
      "from",
      "where",
      "group",
      "by",
      "order",
      "having",
      "limit",
      "offset",
      "join",
      "left",
      "right",
      "inner",
      "outer",
      "cross",
      "on",
      "as",
      "and",
      "or",
      "not",
      "in",
      "is",
      "null",
      "case",
      "when",
      "then",
      "else",
      "end",
      "distinct",
      "with",
      "union",
      "all",
      "desc",
      "asc",
    ];

    const html = highlightWithKeywords(protectedSql.text, keywords, [
      { regex: /\b\d+(?:\.\d+)?\b/g, tokenClass: "number" },
      {
        regex: /\b(count|sum|avg|min|max|coalesce|date_trunc|extract)\b/gi,
        tokenClass: "function",
      },
    ]);
    return restoreSegments(html, protectedSql.tokens);
  }

  function highlightPython(input: string): string {
    const protectedPy = protectSegments(input, [
      { regex: /#.*$/gm, tokenClass: "comment" },
      { regex: /'(?:\\.|[^'\\])*'/g, tokenClass: "string" },
      { regex: /"(?:\\.|[^"\\])*"/g, tokenClass: "string" },
    ]);

    const keywords = [
      "import",
      "from",
      "as",
      "def",
      "class",
      "return",
      "if",
      "elif",
      "else",
      "for",
      "while",
      "in",
      "try",
      "except",
      "finally",
      "with",
      "lambda",
      "yield",
      "pass",
      "break",
      "continue",
      "and",
      "or",
      "not",
      "is",
      "None",
      "True",
      "False",
    ];

    const html = highlightWithKeywords(protectedPy.text, keywords, [
      { regex: /\b\d+(?:\.\d+)?\b/g, tokenClass: "number" },
      {
        regex: /\b(print|len|range|list|dict|set|tuple|int|float|str|plt|np)\b/g,
        tokenClass: "function",
      },
    ]);
    return restoreSegments(html, protectedPy.tokens);
  }

  function highlightCode(input: string, lang: string): string {
    if (!input) {
      return "";
    }
    if (lang.includes("sql")) {
      return highlightSql(input);
    }
    if (lang.includes("python") || lang === "py") {
      return highlightPython(input);
    }
    return escapeHtml(input);
  }

  function stopTimer(): void {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  }

  function startReveal(): void {
    stopTimer();
    rendered = "";
    if (!code) {
      return;
    }

    const chunk = Math.max(1, Math.ceil(code.length / 180));
    timer = setInterval(() => {
      if (rendered.length >= code.length) {
        stopTimer();
        return;
      }
      rendered = code.slice(0, Math.min(code.length, rendered.length + chunk));
    }, 14);
  }

  $: {
    if (code !== lastCode) {
      lastCode = code;
      animatedForCurrentCode = false;
      if (!animate) {
        stopTimer();
        rendered = code;
      }
    }
  }

  $: {
    if (animate && !animatedForCurrentCode) {
      animatedForCurrentCode = true;
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
  <pre><code class={`lang-${languageId}`}>{@html highlighted}</code>{#if isRevealing}<span class="cursor">▋</span>{/if}</pre>
</div>

<style>
  .code-block {
    border-radius: 12px;
    border: 1px solid var(--border-soft);
    background:
      linear-gradient(180deg, rgb(255 255 255 / 2%) 0%, rgb(255 255 255 / 0%) 28%),
      var(--surface-0);
    overflow: hidden;
    box-shadow: inset 0 1px 0 rgb(255 255 255 / 3%);
  }

  .code-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid var(--border-soft);
    padding: 8px 10px;
    font-size: 12px;
    font-family: var(--font-heading);
    color: var(--text-dim);
    letter-spacing: 0.04em;
    text-transform: uppercase;
    background: rgb(255 255 255 / 2%);
  }

  pre {
    margin: 0;
    padding: 12px;
    overflow-x: auto;
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 13px;
    line-height: 1.45;
    min-height: 56px;
    white-space: pre-wrap;
  }

  code {
    font-family: inherit;
  }

  :global(.token.keyword) {
    color: #5ab0ff;
  }

  :global(.token.function) {
    color: #ffb86c;
  }

  :global(.token.string) {
    color: #9be77a;
  }

  :global(.token.number) {
    color: #c79dff;
  }

  :global(.token.comment) {
    color: #7a859c;
    font-style: italic;
  }

  .cursor {
    display: inline-block;
    margin-left: 1px;
    color: var(--accent-blue);
    animation: blink 0.9s step-end infinite;
  }

  @keyframes blink {
    50% {
      opacity: 0;
    }
  }
</style>
