import { describe, expect, it } from "vitest";
import { readNavOpenPreference } from "./navPreference";

describe("readNavOpenPreference", () => {
  it("defaults to open when unset", () => {
    expect(readNavOpenPreference(null)).toBe(true);
  });

  it("respects explicit false", () => {
    expect(readNavOpenPreference("false")).toBe(false);
  });

  it("treats true as open", () => {
    expect(readNavOpenPreference("true")).toBe(true);
  });
});
