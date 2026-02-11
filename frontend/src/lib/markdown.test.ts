import { describe, expect, it } from "vitest";

import { renderMarkdown } from "./markdown";


describe("renderMarkdown link sanitization", () => {
  it("allows https links", () => {
    const html = renderMarkdown("[docs](https://example.com/docs)");

    expect(html).toContain('href="https://example.com/docs"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it("allows mailto links", () => {
    const html = renderMarkdown("[email](mailto:test@example.com)");

    expect(html).toContain('href="mailto:test@example.com"');
    expect(html).toContain(">email</a>");
  });

  it("blocks javascript links", () => {
    const html = renderMarkdown("[oops](javascript:alert('xss'))");

    expect(html).not.toContain("href=");
    expect(html).toContain("<span>oops</span>");
  });

  it("blocks non-whitelisted schemes", () => {
    const html = renderMarkdown("[file](file:///tmp/test.txt)");

    expect(html).not.toContain("href=");
    expect(html).toContain("<span>file</span>");
  });

  it("escapes raw html", () => {
    const html = renderMarkdown("<script>alert('xss')</script>");

    expect(html).toContain("&lt;script&gt;alert(");
    expect(html).toContain("&lt;/script&gt;");
    expect(html).not.toContain("<script>");
  });
});
