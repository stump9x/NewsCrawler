const HIDDEN_TAGS = new Set(["news", "rss", "alleged-claim", "china"]);

/** Region-level geo slugs — deprioritized vs country tags. */
const REGION_GEO_SLUGS = new Set([
  "geo-southeast-asia",
  "geo-asia-pacific",
  "geo-middle-east",
  "geo-europe",
  "geo-latin-america",
  "geo-north-america",
  "geo-africa",
  "geo-emea",
]);

/** geo-* / vietnam → ISO 3166-1 alpha-2 for flag emoji. */
const GEO_ISO2 = {
  vietnam: "VN",
  "geo-united-states": "US",
  "geo-united-kingdom": "GB",
  "geo-canada": "CA",
  "geo-australia": "AU",
  "geo-new-zealand": "NZ",
  "geo-china": "CN",
  "geo-russia": "RU",
  "geo-ukraine": "UA",
  "geo-germany": "DE",
  "geo-france": "FR",
  "geo-italy": "IT",
  "geo-spain": "ES",
  "geo-netherlands": "NL",
  "geo-belgium": "BE",
  "geo-poland": "PL",
  "geo-romania": "RO",
  "geo-switzerland": "CH",
  "geo-austria": "AT",
  "geo-sweden": "SE",
  "geo-norway": "NO",
  "geo-denmark": "DK",
  "geo-finland": "FI",
  "geo-ireland": "IE",
  "geo-portugal": "PT",
  "geo-czech-republic": "CZ",
  "geo-slovakia": "SK",
  "geo-greece": "GR",
  "geo-turkey": "TR",
  "geo-israel": "IL",
  "geo-iran": "IR",
  "geo-iraq": "IQ",
  "geo-saudi-arabia": "SA",
  "geo-united-arab-emirates": "AE",
  "geo-qatar": "QA",
  "geo-india": "IN",
  "geo-pakistan": "PK",
  "geo-bangladesh": "BD",
  "geo-sri-lanka": "LK",
  "geo-japan": "JP",
  "geo-south-korea": "KR",
  "geo-north-korea": "KP",
  "geo-taiwan": "TW",
  "geo-singapore": "SG",
  "geo-malaysia": "MY",
  "geo-indonesia": "ID",
  "geo-thailand": "TH",
  "geo-philippines": "PH",
  "geo-myanmar": "MM",
  "geo-cambodia": "KH",
  "geo-laos": "LA",
  "geo-brazil": "BR",
  "geo-mexico": "MX",
  "geo-argentina": "AR",
  "geo-chile": "CL",
  "geo-colombia": "CO",
  "geo-south-africa": "ZA",
  "geo-nigeria": "NG",
  "geo-kenya": "KE",
  "geo-egypt": "EG",
};

function slugOf(tag) {
  return String(tag?.slug || tag?.name || "").toLowerCase();
}

export function isGeographyTag(tag) {
  const slug = slugOf(tag);
  return slug === "vietnam" || slug.startsWith("geo-");
}

export function isRegionGeographyTag(tag) {
  return REGION_GEO_SLUGS.has(slugOf(tag));
}

export function geographyIso2(tag) {
  const slug = slugOf(tag);
  return GEO_ISO2[slug] || "";
}

/** Regional-indicator flag emoji from ISO2 (optional; UI prefers flagcdn img). */
export function flagEmojiFromIso2(iso2) {
  const code = String(iso2 || "")
    .trim()
    .toUpperCase();
  if (!/^[A-Z]{2}$/.test(code)) return "";
  return String.fromCodePoint(
    ...[...code].map((ch) => 0x1f1e6 - 65 + ch.charCodeAt(0))
  );
}

/** Official flag PNG (flagcdn) — reliable vs emoji fonts on Windows. */
export function geographyFlagUrl(tag, width = 20) {
  const iso = geographyIso2(tag);
  if (!iso) return "";
  const w = Math.max(16, Math.min(Number(width) || 20, 80));
  return `https://flagcdn.com/w${w}/${iso.toLowerCase()}.png`;
}

export function geographyFlagEmoji(tag) {
  const iso = geographyIso2(tag);
  return iso ? flagEmojiFromIso2(iso) : "";
}

/** Preferred Vietnamese display names for monitored geography tags. */
const GEO_LABEL_VI = {
  vietnam: "Việt Nam",
  "geo-united-states": "Mỹ",
  "geo-china": "Trung Quốc",
  "geo-taiwan": "Đài Loan",
  "geo-japan": "Nhật Bản",
  "geo-philippines": "Philippines",
  "geo-laos": "Lào",
  "geo-thailand": "Thái Lan",
  "geo-cambodia": "Campuchia",
  "geo-indonesia": "Indonesia",
  "geo-malaysia": "Malaysia",
  "geo-australia": "Úc",
  "geo-russia": "Nga",
  "geo-ukraine": "Ukraina",
  "geo-myanmar": "Myanmar",
  "geo-southeast-asia": "Đông Nam Á",
  "geo-asia-pacific": "Châu Á - Thái Bình Dương",
};

/**
 * Country filter options for The Wire (priority order, top → bottom).
 * `value` must equal the geography tag slug used for flag chips — backend
 * filters by exact `tags__slug` only (no fuzzy title/payload matching).
 */
export const WIRE_COUNTRY_FILTER_OPTIONS = [
  { value: "geo-china", label: "Trung Quốc" },
  { value: "geo-united-states", label: "Mỹ" },
  { value: "geo-philippines", label: "Philippines" },
  { value: "geo-taiwan", label: "Đài Loan" },
  { value: "geo-thailand", label: "Thái Lan" },
  { value: "geo-indonesia", label: "Indonesia" },
  { value: "geo-malaysia", label: "Malaysia" },
  { value: "vietnam", label: "Việt Nam" },
  { value: "geo-japan", label: "Nhật Bản" },
  { value: "geo-cambodia", label: "Campuchia" },
  { value: "geo-laos", label: "Lào" },
  { value: "geo-australia", label: "Úc" },
  { value: "geo-russia", label: "Nga" },
  { value: "geo-ukraine", label: "Ukraina" },
  { value: "geo-myanmar", label: "Myanmar" },
];

/** Plain country/region name for Chip label (flag rendered separately as img). */
export function geographyTagLabel(tag) {
  const slug = slugOf(tag);
  if (GEO_LABEL_VI[slug]) return GEO_LABEL_VI[slug];
  return slug
    .replace(/^geo-/, "")
    .split("-")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

/** Country tags first, then regions; Vietnam pinned ahead of other countries. */
export function preferCountryGeography(tags) {
  const list = Array.isArray(tags) ? [...tags] : [];
  list.sort((a, b) => {
    const sa = slugOf(a);
    const sb = slugOf(b);
    if (sa === "vietnam" && sb !== "vietnam") return -1;
    if (sb === "vietnam" && sa !== "vietnam") return 1;
    const ra = isRegionGeographyTag(a) ? 1 : 0;
    const rb = isRegionGeographyTag(b) ? 1 : 0;
    return ra - rb;
  });
  return list;
}

/**
 * Country-level geography tags with flags (same set shown on card headers).
 * Regions without a national flag are excluded.
 */
export function wireCountryTags(row, maxCountries = 6) {
  const tags = Array.isArray(row?.tags) ? row.tags : [];
  return preferCountryGeography(
    tags.filter(isGeographyTag).filter((tag) => !isRegionGeographyTag(tag))
  )
    .filter((tag) => geographyFlagUrl(tag, 20))
    .slice(0, Math.max(0, maxCountries));
}

/**
 * Website / KEV / topics first, geography last (reserved).
 * Country tags are preferred over region tags; alleged-claim is hidden.
 * Geography budget matches card header flags so every flag has a tag.
 */
export function orderedWireTags(row, maxTags = 8, maxGeography = 6) {
  const tags = Array.isArray(row?.tags) ? row.tags : [];
  const website = tags.find((tag) => slugOf(tag).startsWith("site-"));
  const geography = wireCountryTags(row, maxGeography);
  const topics = tags.filter((tag) => {
    const slug = slugOf(tag);
    return (
      !slug.startsWith("site-") &&
      !HIDDEN_TAGS.has(slug) &&
      !isGeographyTag(tag)
    );
  });

  const geoSelected = geography;
  const headBudget = Math.max(0, maxTags - geoSelected.length);

  const ordered = [];
  if (website && ordered.length < headBudget) {
    ordered.push({ kind: "website", tag: website });
  }
  if (row?.is_kev && ordered.length < headBudget) {
    ordered.push({ kind: "kev", key: "kev" });
  }
  for (const tag of topics) {
    if (ordered.length >= headBudget) break;
    ordered.push({ kind: "topic", tag });
  }
  ordered.push(
    ...geoSelected.map((tag) => ({ kind: "geography", tag }))
  );
  return ordered.slice(0, Math.max(0, maxTags));
}
