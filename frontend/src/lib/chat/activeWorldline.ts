const ACTIVE_WORLDLINE_STORAGE_KEY = "textql_active_worldline";

export function getActiveWorldlineFromStorage(): string | null {
  if (typeof localStorage === "undefined") {
    return null;
  }
  const worldlineId = localStorage.getItem(ACTIVE_WORLDLINE_STORAGE_KEY);
  if (!worldlineId || worldlineId.trim().length === 0) {
    return null;
  }
  return worldlineId;
}
