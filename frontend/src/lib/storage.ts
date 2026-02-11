export function safeJsonParse<T>(
  raw: string,
  validate?: (value: unknown) => value is T,
): T | null {
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (validate && !validate(parsed)) {
      return null;
    }
    return parsed as T;
  } catch {
    return null;
  }
}

export function getStoredJson<T>(
  key: string,
  validate?: (value: unknown) => value is T,
): T | null {
  if (typeof localStorage === "undefined") {
    return null;
  }
  const raw = localStorage.getItem(key);
  if (!raw) {
    return null;
  }
  return safeJsonParse<T>(raw, validate);
}
