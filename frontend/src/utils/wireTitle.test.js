import { describe, expect, it } from "vitest";
import { displayWireTitle } from "./wireTitle";

describe("displayWireTitle", () => {
  it("shows Vietnamese title when present", () => {
    expect(
      displayWireTitle({
        title: "PLA navy expands South China Sea patrols",
        title_vi: "Hải quân PLA mở rộng tuần tra Biển Đông",
      })
    ).toBe("Hải quân PLA mở rộng tuần tra Biển Đông");
  });

  it("falls back to English while translation is pending", () => {
    expect(
      displayWireTitle({
        title: "US Indo-Pacific force posture update",
        title_vi: "",
        title_vi_status: "pending",
      })
    ).toBe("US Indo-Pacific force posture update");
  });

  it("shows placeholder only when both titles are empty", () => {
    expect(displayWireTitle({ title: "", title_vi: "" })).toBe("Đang dịch…");
  });
});
