import { describe, expect, it } from "vitest";
import { hasInlineMarkdown, splitInlineMarkdown } from "./inlineMarkdown";

describe("splitInlineMarkdown", () => {
  it("renders paired bold without leftover asterisks", () => {
    const parts = splitInlineMarkdown("**Sự kiện then chốt:** còn lại");
    expect(parts).toEqual([
      { type: "strong", value: "Sự kiện then chốt:" },
      { type: "text", value: " còn lại" },
    ]);
  });

  it("handles italic and mixed", () => {
    const parts = splitInlineMarkdown("A *b* and **c** end");
    expect(parts.map((p) => p.type)).toEqual([
      "text",
      "em",
      "text",
      "strong",
      "text",
    ]);
    expect(parts[1].value).toBe("b");
    expect(parts[3].value).toBe("c");
  });

  it("leaves plain text alone", () => {
    expect(splitInlineMarkdown("plain")).toEqual([
      { type: "text", value: "plain" },
    ]);
  });
});

describe("hasInlineMarkdown", () => {
  it("detects bold markers", () => {
    expect(hasInlineMarkdown("**x**")).toBe(true);
    expect(hasInlineMarkdown("plain")).toBe(false);
  });
});
