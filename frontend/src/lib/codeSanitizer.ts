export function sanitizeCodeArtifacts(input: string): string {
  if (!input) {
    return "";
  }

  return input
    .replace(/class="token\s+[^"]*">/g, "")
    .replace(/\bTOK\d+\b/g, "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trimEnd();
}
