import { describe, expect, it } from "vitest";
import {
  flagEmojiFromIso2,
  geographyFlagUrl,
  geographyIso2,
  geographyTagLabel,
  isGeographyTag,
  orderedWireTags,
  preferCountryGeography,
  wireCountryTags,
  WIRE_COUNTRY_FILTER_OPTIONS,
} from "./wireTags";

describe("Wire tag ordering", () => {
  it("hides retired topics on cached articles", () => {
    const result = orderedWireTags({ tags: [
      { slug: "wire-topic-6" }, { slug: "wire-topic-7" },
      { slug: "wire-topic-1a" }, { slug: "geo-china" },
    ] });
    expect(result.map((item) => item.tag.slug)).toEqual(["wire-topic-1a", "geo-china"]);
  });
  it("reserves country geography at the end even when topics are crowded", () => {
    const result = orderedWireTags(
      {
        is_kev: true,
        tags: [
          { slug: "geo-romania" },
          { slug: "geo-china" },
          { slug: "geo-japan" },
          { slug: "ransomware" },
          { slug: "site-example-com" },
          { slug: "data-breach" },
          { slug: "cert" },
          { slug: "geo-europe" },
        ],
      },
      8
    );

    expect(result.map((item) => item.kind)).toEqual([
      "website",
      "kev",
      "topic",
      "topic",
      "topic",
      "geography",
      "geography",
      "geography",
    ]);
    expect(
      result.filter((item) => item.kind === "geography").map((i) => i.tag.slug)
    ).toEqual(["geo-romania", "geo-china", "geo-japan"]);
    expect(result.some((item) => item.tag?.slug === "geo-europe")).toBe(false);
    expect(result).toHaveLength(8);
  });

  it("keeps header flags and bottom country tags in sync", () => {
    const row = {
      tags: [
        { slug: "site-scmp-com" },
        { slug: "geo-china" },
        { slug: "geo-japan" },
        { slug: "geo-russia" },
        { slug: "geo-united-kingdom" },
        { slug: "maritime" },
      ],
    };
    const flags = wireCountryTags(row, 6).map((tag) => tag.slug);
    const bottomGeo = orderedWireTags(row, 8)
      .filter((item) => item.kind === "geography")
      .map((item) => item.tag.slug);
    expect(flags).toEqual([
      "geo-china",
      "geo-japan",
      "geo-russia",
      "geo-united-kingdom",
    ]);
    expect(bottomGeo).toEqual(flags);
  });

  it("hides alleged-claim and still puts geography last", () => {
    const result = orderedWireTags({
      tags: [
        { slug: "alleged-claim" },
        { slug: "geo-romania" },
        { slug: "data-breach" },
        { slug: "site-example-com" },
      ],
    });

    expect(result.map((item) => item.kind)).toEqual([
      "website",
      "topic",
      "geography",
    ]);
    expect(result.some((item) => item.tag?.slug === "alleged-claim")).toBe(
      false
    );
  });

  it("prefers country tags over regions", () => {
    const ordered = preferCountryGeography([
      { slug: "geo-europe" },
      { slug: "geo-romania" },
      { slug: "vietnam" },
    ]);
    expect(ordered.map((t) => t.slug)).toEqual([
      "vietnam",
      "geo-romania",
      "geo-europe",
    ]);
  });

  it("deduplicates hashtags and suppresses generic labels beside canonical topics", () => {
    const result = orderedWireTags({
      tags: [
        { slug: "wire-topic-4a", name: "Diễn tập" },
        { slug: "wire-topic-4a", name: "Diễn tập" },
        { slug: "exercises", name: "Exercises" },
        { slug: "geo-china", name: "China" },
        { slug: "geo-china", name: "China" },
      ],
    });
    expect(result.filter((item) => item.kind === "topic").map((item) => item.tag.slug)).toEqual([
      "wire-topic-4a",
    ]);
    expect(result.filter((item) => item.kind === "geography").map((item) => item.tag.slug)).toEqual([
      "geo-china",
    ]);
  });

  it("exposes ISO2 and flagcdn URL for official flag images", () => {
    expect(isGeographyTag({ slug: "vietnam" })).toBe(true);
    expect(geographyIso2({ slug: "geo-united-states" })).toBe("US");
    expect(geographyIso2({ slug: "vietnam" })).toBe("VN");
    expect(geographyFlagUrl({ slug: "geo-canada" }, 20)).toBe(
      "https://flagcdn.com/w20/ca.png"
    );
    expect(flagEmojiFromIso2("US")).toBe("🇺🇸");
    expect(geographyTagLabel({ slug: "geo-united-states" })).toBe("Mỹ");
    expect(geographyTagLabel({ slug: "vietnam" })).toBe("Việt Nam");
    expect(geographyTagLabel({ slug: "geo-china" })).toBe("Trung Quốc");
    expect(geographyTagLabel({ slug: "geo-taiwan" })).toBe("Đài Loan");
    expect(geographyTagLabel({ slug: "geo-japan" })).toBe("Nhật Bản");
    expect(geographyTagLabel({ slug: "geo-laos" })).toBe("Lào");
    expect(geographyTagLabel({ slug: "geo-thailand" })).toBe("Thái Lan");
    expect(geographyTagLabel({ slug: "geo-cambodia" })).toBe("Campuchia");
    expect(geographyTagLabel({ slug: "geo-australia" })).toBe("Úc");
    expect(geographyTagLabel({ slug: "geo-ukraine" })).toBe("Ukraina");
    expect(geographyTagLabel({ slug: "geo-myanmar" })).toBe("Myanmar");
  });

  it("country filter options use flag geography slugs only", () => {
    expect(WIRE_COUNTRY_FILTER_OPTIONS.map((o) => o.value)).toEqual([
      "geo-china",
      "geo-united-states",
      "geo-taiwan",
      "geo-japan",
      "geo-australia",
      "geo-north-korea",
      "geo-south-korea",
      "vietnam",
      "geo-philippines",
      "geo-thailand",
      "geo-indonesia",
      "geo-malaysia",
      "geo-singapore",
      "geo-cambodia",
      "geo-laos",
      "geo-myanmar",
    ]);
    for (const opt of WIRE_COUNTRY_FILTER_OPTIONS) {
      expect(geographyIso2({ slug: opt.value })).toBeTruthy();
      expect(geographyTagLabel({ slug: opt.value })).toBe(opt.label);
    }
  });
});
