import { afterEach, describe, expect, it, vi } from "vitest";
import { resolveNotebookOrigin } from "./notebookUrl.js";

describe("resolveNotebookOrigin", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.stubEnv("VITE_NOTEBOOK_URL", "");
  });

  it("uses VITE_NOTEBOOK_URL when set", () => {
    vi.stubEnv("VITE_NOTEBOOK_URL", "http://custom.example:3000/");
    expect(resolveNotebookOrigin()).toBe("http://custom.example:3000");
  });

  it("maps localhost to notebook.localhost", () => {
    vi.stubEnv("VITE_NOTEBOOK_URL", "");
    vi.stubGlobal("window", {
      location: { protocol: "http:", hostname: "localhost", port: "3000" },
    });
    expect(resolveNotebookOrigin()).toBe("http://notebook.localhost:3000");
  });

  it("maps public IP to sslip.io notebook host", () => {
    vi.stubEnv("VITE_NOTEBOOK_URL", "");
    vi.stubGlobal("window", {
      location: { protocol: "http:", hostname: "107.161.168.82", port: "3000" },
    });
    expect(resolveNotebookOrigin()).toBe(
      "http://notebook.107.161.168.82.sslip.io:3000"
    );
  });
});
