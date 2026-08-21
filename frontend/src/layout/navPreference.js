export const NAV_OPEN_STORAGE_KEY = "bs-nav-open";

export function readNavOpenPreference(storedValue) {
  return storedValue !== "false";
}

export function writeNavOpenPreference(open) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(NAV_OPEN_STORAGE_KEY, open ? "true" : "false");
}

export function loadNavOpenPreference() {
  if (typeof window === "undefined") return true;
  return readNavOpenPreference(window.localStorage.getItem(NAV_OPEN_STORAGE_KEY));
}
