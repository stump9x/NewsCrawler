import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

describe("application branding", () => {
  it("uses NewsCrawler and contains no legacy product branding", () => {
    const files = [
      "index.html",
      "src/layout/AppShell.jsx",
      "src/pages/LoginPage.jsx",
      "src/api/client.js",
    ];
    const content = files
      .map((file) => readFileSync(resolve(process.cwd(), file), "utf8"))
      .join("\n");
    expect(content).toContain("NewsCrawler");
    expect(content.toLowerCase()).not.toContain("breachsentinel");
    expect(content).not.toContain("bs_api_token");
    expect(content).toContain("nc_api_token");
  });
});
