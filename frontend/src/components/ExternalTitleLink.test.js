import { describe, expect, it } from "vitest";
import { resolveThreatHref } from "./ExternalTitleLink";

describe("resolveThreatHref", () => {
  it("uses the source detail URL when available", () => {
    expect(
      resolveThreatHref({
        source: "news",
        source_url: "https://www.twz.com/example",
      })
    ).toBe("https://www.twz.com/example");
  });

  it("does not invent fallback URLs for missing source links", () => {
    expect(resolveThreatHref({ source: "news", source_url: "" })).toBe("");
    expect(resolveThreatHref({ source: "ransomware", source_url: "" })).toBe("");
  });

  it("absolutizes relative MOD.go.jp links using feed_url", () => {
    expect(
      resolveThreatHref({
        source: "news",
        source_url: "/j/press/news/2026/07/15a.html",
        raw_payload: {
          feed: "japan-mod",
          feed_url: "https://www.mod.go.jp/j/rss/news.xml",
          link: "/j/press/news/2026/07/15a.html",
        },
      })
    ).toBe("https://www.mod.go.jp/j/press/news/2026/07/15a.html");
  });
});
