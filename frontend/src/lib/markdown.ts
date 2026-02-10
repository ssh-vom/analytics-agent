import { Marked } from "marked";

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const marked = new Marked({
  gfm: true,
  breaks: true,
});

const renderer = {
  html(html: string): string {
    return escapeHtml(html);
  },
  link({ href, title, tokens }: { href: string; title: string | null; tokens: unknown[] }): string {
    const safeHref = escapeHtml(href ?? "");
    const safeTitle = title ? ` title="${escapeHtml(title)}"` : "";
    const text = marked.parser(tokens as never);
    return `<a href="${safeHref}"${safeTitle} target="_blank" rel="noopener noreferrer">${text}</a>`;
  },
};

marked.use({ renderer });

export function renderMarkdown(text: string): string {
  if (!text) {
    return "";
  }
  return marked.parse(text) as string;
}
