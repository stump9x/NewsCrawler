/**
 * Resolve Open Notebook public origin on the same port as NewsCrawler (3000)
 * via Host subdomain — no separate :8502 required in the browser.
 *
 * Examples:
 *   localhost:3000        → http://notebook.localhost:3000
 *   107.x.x.x:3000        → http://notebook.107.x.x.x.sslip.io:3000
 *   app.example.com:3000  → http://notebook.app.example.com:3000
 */
export function resolveNotebookOrigin() {
  const configured = (import.meta.env.VITE_NOTEBOOK_URL || "").trim();
  if (configured) {
    return configured.replace(/\/$/, "");
  }

  if (typeof window === "undefined") {
    return "";
  }

  const { protocol, hostname, port } = window.location;
  const portPart =
    port && port !== "80" && port !== "443" ? `:${port}` : "";

  // Already on the notebook vhost
  if (
    hostname === "notebook.localhost" ||
    hostname.startsWith("notebook.")
  ) {
    return `${protocol}//${hostname}${portPart}`;
  }

  if (hostname === "localhost" || hostname === "127.0.0.1") {
    return `${protocol}//notebook.localhost${portPart}`;
  }

  // Public IP → free wildcard DNS (sslip.io → same IP)
  if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(hostname)) {
    return `${protocol}//notebook.${hostname}.sslip.io${portPart}`;
  }

  return `${protocol}//notebook.${hostname}${portPart}`;
}
