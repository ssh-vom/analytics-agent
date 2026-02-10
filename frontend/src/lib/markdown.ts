import { Marked } from "marked";

const ALLOWED_LINK_PROTOCOLS = new Set(["http:", "https:", "mailto:"]);

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function sanitizeLinkHref(href: string | null | undefined): string | null {
  const trimmed = (href ?? "").trim();
  if (!trimmed) {
    return null;
  }
  if (!/^[a-zA-Z][a-zA-Z\d+.-]*:/.test(trimmed)) {
    return null;
  }

  try {
    const parsed = new URL(trimmed);
    if (!ALLOWED_LINK_PROTOCOLS.has(parsed.protocol)) {
      return null;
    }
    return trimmed;
  } catch {
    return null;
  }
}

const marked = new Marked({
  gfm: true,
  breaks: true,
});

const renderer = {
  html(token: unknown): string {
    if (typeof token === "string") {
      return escapeHtml(token);
    }
    if (typeof token === "object" && token !== null) {
      const rawValue = (token as { raw?: unknown; text?: unknown }).raw;
      if (typeof rawValue === "string") {
        return escapeHtml(rawValue);
      }
      const textValue = (token as { text?: unknown }).text;
      if (typeof textValue === "string") {
        return escapeHtml(textValue);
      }
    }
    return "";
  },
  link(token: unknown): string {
    const linkToken =
      typeof token === "object" && token !== null
        ? (token as { href?: unknown; title?: unknown; tokens?: unknown[] })
        : {};
    const safeHref = sanitizeLinkHref(
      typeof linkToken.href === "string" ? linkToken.href : null,
    );
    const safeTitle =
      typeof linkToken.title === "string"
        ? ` title="${escapeHtml(linkToken.title)}"`
        : "";
    const text = marked.parser((linkToken.tokens ?? []) as never);
    if (!safeHref) {
      return `<span>${text}</span>`;
    }
    return `<a href="${escapeHtml(safeHref)}"${safeTitle} target="_blank" rel="noopener noreferrer">${text}</a>`;
  },
};

marked.use({ renderer });

export function renderMarkdown(text: string): string {
  if (!text) {
    return "";
  }
  return marked.parse(text) as string;
}
