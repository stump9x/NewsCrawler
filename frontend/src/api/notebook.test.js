import { describe, expect, it } from "vitest";
import {
  buildNotebookContextConfig,
  filterBuiltNotebookContext,
  normalizeNotebookSourceId,
  notebookScopedStyleHint,
  cleanSourcePlainText,
  sanitizeStudioOutput,
  cutPlainTextAtTitle,
  textLooksVietnamese,
  extractClearSentences,
  buildSourceReadableExcerpt,
  prepareTransformSourcePayload,
  orderModelsByHealthyProviders,
  pickTransformPreferredModelId,
  pickTransformFallbackChain,
  pickProviderFallbackChain,
  isNotebookStudioModel,
  transformMaxTokensForPreset,
  TRANSFORM_PROVIDER_ORDER,
  shrinkNotebookChatContext,
  mergeCachedBodiesIntoContext,
  splitChatRaceChain,
  splitTransformRaceChain,
  buildTransformModelTryOrder,
  isSimpleNotebookChatQuery,
  isMainContentDigestQuery,
  isSocialChitchatQuery,
  buildTextFragmentHref,
  notebookAnswerQualityIssue,
  sanitizeNotebookAnswer,
  buildDeterministicGroundedAnswer,
  CHAT_PROVIDER_ORDER,
  CHAT_HANG_TIMEOUT_MS,
  formatNotebookCitations,
} from "./notebook";

describe("normalizeNotebookSourceId", () => {
  it("adds source: prefix when missing", () => {
    expect(normalizeNotebookSourceId("abc")).toBe("source:abc");
  });
  it("keeps existing prefix", () => {
    expect(normalizeNotebookSourceId("source:abc")).toBe("source:abc");
  });
});

describe("isNotebookStudioModel", () => {
  const model = (provider, name) => ({ type: "language", provider, name });

  it("keeps paid ShopAIKey models and the fast Groq fallback", () => {
    expect(isNotebookStudioModel(model("openai_compatible", "gpt-5-mini"))).toBe(true);
    expect(isNotebookStudioModel(model("openai_compatible", "qwen3-235b-a22b"))).toBe(true);
    expect(isNotebookStudioModel(model("groq", "llama-3.1-8b-instant"))).toBe(true);
  });

  it("hides unavailable, unreliable and slow interactive choices", () => {
    expect(isNotebookStudioModel(model("openai_compatible", "zai-glm-4.7"))).toBe(false);
    expect(isNotebookStudioModel(model("openrouter", "openrouter/free"))).toBe(false);
    expect(isNotebookStudioModel(model("ollama", "qwen2.5:3b"))).toBe(false);
    expect(isNotebookStudioModel(model("groq", "llama-3.3-70b-versatile"))).toBe(false);
  });
});

describe("buildNotebookContextConfig", () => {
  const sources = [
    { id: "source:a", title: "A" },
    { id: "source:b", title: "B" },
    { id: "c", title: "C" },
  ];

  it("defaults to insights for every listed source when nothing selected", () => {
    const cfg = buildNotebookContextConfig(sources, []);
    expect(cfg.notes).toEqual({});
    expect(cfg.sources).toEqual({
      "source:a": "insights",
      "source:b": "insights",
      "source:c": "insights",
    });
  });

  it("scopes to selected ids with full content and marks others not in context", () => {
    const cfg = buildNotebookContextConfig(sources, ["source:b"]);
    expect(cfg.sources["source:a"]).toBe("not in context");
    expect(cfg.sources["source:b"]).toBe("full content");
    expect(cfg.sources["source:c"]).toBe("not in context");
  });

  it("matches bare selected ids against prefixed source ids", () => {
    const cfg = buildNotebookContextConfig(sources, ["b", "c"]);
    expect(cfg.sources["source:a"]).toBe("not in context");
    expect(cfg.sources["source:b"]).toBe("full content");
    expect(cfg.sources["source:c"]).toBe("full content");
  });

  it("still includes a selected id missing from sources[]", () => {
    const cfg = buildNotebookContextConfig(sources, ["source:missing"]);
    expect(cfg.sources["source:missing"]).toBe("full content");
    expect(cfg.sources["source:a"]).toBe("not in context");
  });
});

describe("filterBuiltNotebookContext", () => {
  it("passes through when unscoped", () => {
    const ctx = {
      sources: [{ id: "source:a" }, { id: "source:b" }],
      notes: [],
    };
    expect(filterBuiltNotebookContext(ctx, [])).toEqual(ctx);
  });

  it("drops unselected sources when scoped", () => {
    const ctx = {
      sources: [
        { id: "source:a", title: "A" },
        { id: "source:b", title: "B" },
      ],
      notes: [{ id: "note:1" }],
    };
    expect(filterBuiltNotebookContext(ctx, ["source:b"])).toEqual({
      sources: [{ id: "source:b", title: "B" }],
      notes: [{ id: "note:1" }],
    });
  });
});

describe("notebookScopedStyleHint", () => {
  it("is empty when unscoped", () => {
    expect(notebookScopedStyleHint([{ id: "source:a" }], [])).toBe("");
  });
  it("names selected sources", () => {
    const hint = notebookScopedStyleHint(
      [{ id: "source:a", title: "Alpha" }],
      ["source:a"]
    );
    expect(hint).toContain("source:a");
    expect(hint).toContain("Alpha");
    expect(hint).toContain("Phạm vi BẮT BUỘC");
  });
});

describe("cleanSourcePlainText / transform preview", () => {
  it("strips HTML and markdown into plain prose", () => {
    const raw =
      "<h1>Hello</h1><p>First <b>sentence</b> about ships.</p>" +
      "<p>Second sentence with a [link](https://x.test).</p>";
    const plain = cleanSourcePlainText(raw);
    expect(plain).not.toMatch(/</);
    expect(plain).toContain("Hello");
    expect(plain).toContain("First sentence about ships.");
    expect(plain).toContain("Second sentence");
    expect(plain).not.toContain("https://");
  });

  it("strips skip-to-content nav chrome blobs", () => {
    const raw =
      "Skip to content Search SubscribeSign In EnergySciencePolitics " +
      "Australia announced a coastal defence policy covering northern approaches.";
    const plain = cleanSourcePlainText(raw);
    expect(plain).toContain("coastal defence policy");
    expect(plain.toLowerCase()).not.toContain("skip to content");
  });

  it("strips Breaking Defense social/nav/ad chrome for Transform", () => {
    const raw = [
      "Open navigation",
      "Close navigation",
      "Air Land Naval Space Cyber",
      "Twitter",
      "Facebook",
      "YouTube",
      "RSS",
      "LinkedIn",
      "Envelope",
      "Share Options",
      "Copy Link",
      "Email",
      "https://securepubads.g.doubleclick.net/gampad/ads?iu=/123/bd",
      "Learn More >>",
      "Oracle inks $7 billion deal with Pentagon for cloud",
      "",
      "Oracle Corp. has signed a roughly $7 billion agreement with the Pentagon to provide cloud computing services across classified networks.",
      "Officials said the award will support warfighter data workloads and accelerate migration away from legacy systems.",
    ].join("\n");
    const plain = cleanSourcePlainText(raw);
    expect(plain).toContain("$7 billion agreement");
    expect(plain).toContain("warfighter data workloads");
    for (const junk of [
      "Twitter",
      "Facebook",
      "YouTube",
      "LinkedIn",
      "Envelope",
      "Open navigation",
      "Share Options",
      "doubleclick",
      "Learn More",
    ]) {
      expect(plain).not.toContain(junk);
    }
    const prepared = prepareTransformSourcePayload(
      { title: "Oracle inks $7 billion deal with Pentagon for cloud", full_text: raw },
      { insights: [] }
    );
    expect(prepared.inputText).toContain("$7 billion");
    expect(prepared.inputText).not.toContain("Twitter");
    expect(prepared.inputText).not.toContain("doubleclick");
  });

  it("cuts at title: drops Markdown Content + bullet social nav before headline", () => {
    const title = "Oracle inks $7 billion deal with Pentagon for cloud";
    const raw = [
      "Markdown Content:",
      "• Twitter",
      "• Facebook",
      "• YouTube",
      "• RSS",
      "• LinkedIn",
      "• Envelope",
      "• About Us",
      "• Webinars",
      "Open navigationBreaking DefenseToggle Search",
      "Europe EditionNewsletter Signup",
      "• Air • Land • Naval • Space • Cyber",
      "Search for: Search",
      "• Farnborough",
      "Networks & Digital Warfare",
      title,
      "",
      "Oracle Corp. has signed a roughly $7 billion agreement with the Pentagon to provide cloud computing services across classified networks.",
      "Officials said the award will support warfighter data workloads.",
    ].join("\n");
    const plain = cleanSourcePlainText(raw, { titleHint: title });
    expect(plain.trim().startsWith("Oracle inks")).toBe(true);
    expect(plain).toContain("$7 billion agreement");
    for (const junk of [
      "Markdown Content",
      "Twitter",
      "Facebook",
      "YouTube",
      "Open navigation",
      "About Us",
      "Webinars",
      "Farnborough",
      "Networks & Digital Warfare",
      "Europe Edition",
    ]) {
      expect(plain).not.toContain(junk);
    }
    const prepared = prepareTransformSourcePayload(
      { title, full_text: raw },
      { insights: [] }
    );
    expect(prepared.inputText.trim().startsWith("Oracle inks")).toBe(true);
    expect(prepared.inputText).not.toContain("Twitter");
    expect(prepared.inputText).not.toContain("Open navigation");
    expect(cutPlainTextAtTitle(raw, title).trim().startsWith("Oracle inks")).toBe(
      true
    );
  });

  it("G-BAM: keeps title+full body; drops Topics/nav/sponsored (not title-only)", () => {
    const title =
      "Pentagon eyes November to demo ground-launched, precision strike weapons";
    const raw = [
      "• News Video: The Weekly Break Out",
      "• Special Features",
      "",
      "• Air",
      "• Land",
      "• Naval",
      "• Space",
      "• Networks",
      "• AI",
      "• Business",
      "• Congress",
      "• Pentagon",
      "• Global",
      "",
      "• Air & Space Chiefs",
      "• Manned-Unmanned Teaming",
      "",
      "Pentagon",
      "",
      title,
      "",
      "Aligned with the new autonomy czar shop, the department plans to spend $250 million evaluating options and moving out with initial deals for its new Ground-Based Affordable Mass initiative.",
      "",
      "By Ashley Roque on July 24, 2026 12:05 pm",
      "",
      "Share a link to this article",
      "• X",
      "",
      "Seal of the Pentagon on display at the Pentagon visitor center. (Photo by Trevor Raney Digital Media Division)",
      "",
      "WASHINGTON — The Defense Innovation Unit (DIU) today announced the new Ground-Based Affordable Mass (G-BAM) challenge for long-range precision strike systems that can be demonstrated within three months and ready for production within 12-18 months.",
      "",
      "The Pentagon plans to spend $250 million evaluating options and making early follow-on deals for the new ground-launched, precision strike challenge.",
      "",
      "As for secondary attributes, the Defense Department is looking for systems with automatic target recognition and autonomy-enabled terminal guidance.",
      "",
      "presented by",
      "",
      "Sponsored Post,Networks & Digital Warfare",
      "",
      "How defensive cyber responds to hockey-stick growth of AI-driven threats",
      "",
      "Artificial intelligence is reshaping adversarial cyber operations at machine speed, requiring defenders to increase operational tempo while imposing pain and cost.",
      "",
      "By Breaking Defense",
      "",
      "Interested companies are being asked to submit white papers outlining their solutions. DoD then plans to award an unspecified number of companies with $250,000 to participate in a Phase 2 flight demonstration in early November.",
      "",
      'Top performers there could then receive $5 million to "immediately deliver" operational test quantities to units.',
      "",
      "Topics:",
      "• Army",
      "• autonomous",
      "• C2",
      "• DARPA Cyber Grand Challenge",
      "• Defense Innovation Unit DIU",
      "• Ground-Based Affordable Mass (G-BAM)",
      "• Pentagon",
      "• precision fires",
    ].join("\n");
    const plain = cleanSourcePlainText(raw, { titleHint: title });
    expect(plain.trim().startsWith("Pentagon eyes November")).toBe(true);
    expect(plain.length).toBeGreaterThan(800);
    expect(plain).toContain("WASHINGTON");
    expect(plain).toContain("G-BAM");
    expect(plain).toContain("white papers");
    expect(plain).toContain("Phase 2");
    expect(plain).toContain("$5 million");
    for (const junk of [
      "News Video: The Weekly Break Out",
      "Special Features",
      "Air & Space Chiefs",
      "Share a link",
      "presented by",
      "Sponsored Post",
      "hockey-stick growth",
      "Topics:",
      "DARPA Cyber Grand Challenge",
      "precision fires",
    ]) {
      expect(plain).not.toContain(junk);
    }
    const prepared = prepareTransformSourcePayload(
      { title, full_text: raw },
      { insights: [] }
    );
    expect(prepared.inputText.length).toBeGreaterThan(800);
    expect(prepared.inputText).toContain("white papers");
    expect(prepared.inputText).not.toContain("Topics:");
  });

  it("Interesting Engineering lithium plant: full body, skip image/READ NEXT/Recommended", () => {
    const title =
      "US firm targets 1 gigawatt-hour capacity at lithium plant for military applications";
    const raw = [
      "NEWS VIDEOS ENERGY SCIENCE MILITARY HEALTH TRANSPORTATION SPACE",
      "FUTURE OF DEFENSE",
      title,
      "The facility will manufacture specialized lithium-ion cells as per military specifications.",
      "READ NEXT: Next-gen maritime counter-drone system to bolster defense with precision-guided missile systems",
      "By Aman Tripathi",
      "GET YOUR NEWS FROM INTERESTING ENGINEERING",
      "![hero](https://cdn.example.com/hero-lithium.jpg)",
      "US-based EnerSys has revised its strategy for a planned lithium cell manufacturing facility in Greenville, South Carolina. The updated plan shifts the primary purpose of the plant toward aerospace, defense, and specialized industrial energy applications.",
      "The company received a revised grant of approximately $150 million from the United States Department of Energy to support this updated direction.",
      "Advertisement",
      "![mid](https://cdn.example.com/mid-battery.jpg)",
      "The revised strategy places strong emphasis on technical requirements specific to military and aerospace hardware. The facility will manufacture specialized lithium-ion cells designed to meet strict defense specifications.",
      "Under the updated plan, EnerSys expects the facility to have an initial production capacity of approximately 1 gigawatt hour, purpose built to meet the specialized requirements of defense applications.",
      "Once fully operational, the plant will supply domestic lithium-ion cells to defense customers requiring secure, foreign-independent energy storage technologies.",
      "RECOMMENDED ARTICLES",
      "DNA Discovery Reveals Truth About Native Americans",
      "blueprint BY INTERESTING ENGINEERING",
      "Get the latest in engineering, tech, space & science - delivered daily to your inbox.",
      "Sign up for free",
      "0 COMMENT",
      "By Aman Tripathi",
      "An active and versatile journalist and news editor. He has covered technology and defense.",
    ].join("\n");
    const plain = cleanSourcePlainText(raw, { titleHint: title });
    expect(plain.trim().startsWith("US firm targets")).toBe(true);
    expect(plain.length).toBeGreaterThan(500);
    const bodyOnly = plain.startsWith(title)
      ? plain.slice(title.length).trim()
      : plain;
    expect(bodyOnly.length).toBeGreaterThan(400);
    expect(plain).toContain("EnerSys");
    expect(plain).toContain("Greenville");
    expect(plain).toContain("energy storage technologies");
    expect(plain).not.toContain("READ NEXT");
    expect(plain).not.toContain("RECOMMENDED ARTICLES");
    expect(plain).not.toContain("DNA Discovery");
    expect(plain).not.toContain("cdn.example.com");
    expect(plain.toLowerCase()).not.toContain("blueprint");
    const prepared = prepareTransformSourcePayload(
      { title, full_text: raw },
      { insights: [] }
    );
    expect(prepared.inputText.length).toBeGreaterThan(500);
    expect(prepared.inputText).toContain("energy storage technologies");
    expect(prepared.inputText).not.toContain("RECOMMENDED ARTICLES");
    const bodyAfterTitle = prepared.inputText.startsWith(title)
      ? prepared.inputText.slice(title.length).trim()
      : prepared.inputText;
    expect(bodyAfterTitle.length).toBeGreaterThan(400);
  });

  it("detects Vietnamese and prefers VN insight for preview", () => {
    expect(textLooksVietnamese("Tàu sân bay tập trận")).toBe(true);
    expect(textLooksVietnamese("Carrier strike group")).toBe(false);
    const excerpt = buildSourceReadableExcerpt({
      title: "Drill near Taiwan",
      plainText:
        "The carrier group sailed east. Officials declined to comment. Markets rose.",
      insights: [
        {
          content:
            "Nhóm tàu sân bay diễn tập phía đông. Giới chức từ chối bình luận. Thị trường tăng điểm.",
        },
      ],
    });
    expect(excerpt.language).toBe("vi");
    expect(excerpt.fromInsight).toBe(true);
    expect(excerpt.preview).toContain("Tiêu đề:");
    expect(excerpt.preview).toContain("Nhóm tàu sân bay");
    expect(excerpt.sentenceCount).toBeGreaterThanOrEqual(3);
  });

  it("extracts 3–8 sentences from English when no insight", () => {
    const text =
      "Alpha event happened today. Bravo forces moved north. " +
      "Charlie reported losses. Delta denied the claim. " +
      "Echo markets reacted. Foxtrot talks continue. " +
      "Golf weather cleared. Hotel units redeployed.";
    const sents = extractClearSentences(text, { min: 3, max: 8 });
    expect(sents.length).toBeGreaterThanOrEqual(3);
    expect(sents.length).toBeLessThanOrEqual(8);
  });

  it("prepareTransformSourcePayload returns cleaned input + preview", () => {
    const prepared = prepareTransformSourcePayload(
      {
        title: "Test",
        full_text:
          "<p>One clear sentence about policy.</p><p>Two more facts here now.</p><p>Three outcomes were listed today.</p>",
      },
      { insights: [] }
    );
    expect(prepared.inputText).not.toMatch(/</);
    expect(prepared.preview).toContain("Tiêu đề: Test");
    expect(prepared.previewMeta.sentenceCount).toBeGreaterThanOrEqual(1);
  });
});

describe("sanitizeStudioOutput", () => {
  it("removes paired and stray double-asterisk markers", () => {
    expect(
      sanitizeStudioOutput(
        "**Tóm tắt**\n\nNội dung chính.\n\n**Sự kiện then chốt**\n- Dữ kiện**"
      )
    ).toBe("Tóm tắt\n\nNội dung chính.\n\nSự kiện then chốt\n- Dữ kiện");
  });
});

describe("orderModelsByHealthyProviders", () => {
  it("puts healthy providers first without dropping the chain", () => {
    const byId = {
      a: { id: "a", type: "language", provider: "ollama", name: "q" },
      b: { id: "b", type: "language", provider: "groq", name: "g" },
      c: {
        id: "c",
        type: "language",
        provider: "openai_compatible",
        name: "gpt-oss-120b",
      },
    };
    const ordered = orderModelsByHealthyProviders(
      ["a", "b", "c"],
      {
        healthy: ["cerebras", "groq"],
        try_order: ["cerebras", "groq", "openrouter", "ollama"],
      },
      byId
    );
    expect(ordered).toHaveLength(3);
    expect(ordered[0]).toBe("c");
    expect(ordered).toContain("a");
    expect(ordered[ordered.length - 1]).toBe("a");
  });

  it("always demotes ollama behind cloud even when try_order lists it first", () => {
    const byId = {
      a: { id: "a", type: "language", provider: "ollama", name: "qwen2.5:1.5b" },
      o: { id: "o", type: "language", provider: "openrouter", name: "free" },
    };
    const ordered = orderModelsByHealthyProviders(
      ["a", "o"],
      {
        healthy: ["ollama", "openrouter"],
        try_order: ["ollama", "openrouter", "groq", "cerebras"],
      },
      byId
    );
    expect(ordered[0]).toBe("o");
    expect(ordered[1]).toBe("a");
  });
});

describe("isSocialChitchatQuery", () => {
  it("matches EN/VI social rapport", () => {
    for (const q of [
      "Xin chào",
      "Hello",
      "Hi",
      "Bạn là ai?",
      "Cảm ơn",
      "Tạm biệt",
      "Bạn khỏe không?",
      "Ok",
      "Test",
      "how are you",
      "thanks",
      "bye",
      "hôm nay thế nào?",
      "bạn thích gì?",
      "kể chuyện vui",
      "tell me a joke",
    ]) {
      expect(isSocialChitchatQuery(q)).toBe(true);
    }
  });

  it("rejects notebook knowledge / source questions", () => {
    for (const q of [
      "Tóm tắt các nguồn",
      "So sánh bài A và B",
      "Nội dung chính",
      "Summarize this article",
      "Cho tôi biết về Oracle cloud deal",
      "Phân tích nguồn trong notebook",
      "https://example.com/story",
    ]) {
      expect(isSocialChitchatQuery(q)).toBe(false);
    }
  });
});

describe("isMainContentDigestQuery", () => {
  it("matches VN/EN digest intents with 1–2 sources", () => {
    expect(
      isMainContentDigestQuery("noi dung chinh", {
        selectedSourceIds: ["source:a"],
      })
    ).toBe(true);
    expect(
      isMainContentDigestQuery("nội dung chính", {
        selectedSourceIds: ["source:a"],
      })
    ).toBe(true);
    expect(
      isMainContentDigestQuery("summarize", {
        selectedSourceIds: ["a", "b"],
      })
    ).toBe(true);
  });

  it("allows whole notebook when ≤2 sources (none checked)", () => {
    expect(
      isMainContentDigestQuery("tóm tắt", {
        selectedSourceIds: [],
        sourceCount: 1,
      })
    ).toBe(true);
    expect(
      isMainContentDigestQuery("main content", {
        selectedSourceIds: [],
        sourceCount: 2,
      })
    ).toBe(true);
    expect(
      isMainContentDigestQuery("tóm tắt", {
        selectedSourceIds: [],
        sourceCount: 5,
      })
    ).toBe(false);
  });

  it("rejects deep multi-select", () => {
    expect(
      isMainContentDigestQuery("main content", {
        selectedSourceIds: ["a", "b", "c"],
      })
    ).toBe(false);
  });
});

describe("isSimpleNotebookChatQuery", () => {
  it("treats main-content as simple", () => {
    expect(
      isSimpleNotebookChatQuery("tom tat", { selectedSourceIds: ["source:x"] })
    ).toBe(true);
  });
});

describe("shrinkNotebookChatContext", () => {
  it("truncates long full_text for scoped chat", () => {
    const long = "x".repeat(20_000);
    const out = shrinkNotebookChatContext(
      { sources: [{ id: "source:a", full_text: long }], notes: [] },
      { scoped: true }
    );
    expect(out.sources[0].full_text.length).toBeLessThan(long.length);
    expect(out.sources[0].full_text).toContain("đã cắt");
  });

  it("uses a tighter cap for whole-notebook", () => {
    const long = "y".repeat(10_000);
    const out = shrinkNotebookChatContext(
      { sources: [{ id: "source:a", full_text: long }], notes: [] },
      { scoped: false }
    );
    expect(out.sources[0].full_text.length).toBeLessThan(5000);
  });
});

describe("mergeCachedBodiesIntoContext", () => {
  it("fills weak full_text from cache items", () => {
    const body = "Australia coastal defence policy ".repeat(20);
    const out = mergeCachedBodiesIntoContext(
      { sources: [{ id: "source:a", title: "A", full_text: "" }], notes: [] },
      [{ ok: true, source_id: "source:a", text: body, cache_hit: true }]
    );
    expect(out.sources[0].full_text).toContain("coastal defence");
    expect(out.sources[0]._body_from_cache).toBe(true);
  });

  it("prefers Transform cache over existing Open Notebook full_text", () => {
    const existing = "Existing notebook body text ".repeat(20);
    const cached = "Cached Transform-cleaned coastal defence body ".repeat(12);
    const out = mergeCachedBodiesIntoContext(
      { sources: [{ id: "source:a", full_text: existing }], notes: [] },
      [
        {
          ok: true,
          source_id: "source:a",
          text: cached,
          cache_hit: true,
          backend: "cache",
        },
      ]
    );
    expect(out.sources[0].full_text).toContain("Transform-cleaned");
    expect(out.sources[0]._body_from_cache).toBe(true);
  });
});

describe("splitChatRaceChain", () => {
  it("uses ShopAIKey first with free cloud fallbacks", () => {
    expect(CHAT_PROVIDER_ORDER).toEqual([
      "shopaikey",
      "groq",
      "openrouter",
    ]);
  });

  it("selects ShopAIKey fast/deep models by query profile", () => {
    const models = [
      {
        id: "fast",
        type: "language",
        provider: "openai_compatible",
        name: "qwen-flash",
      },
      {
        id: "deep",
        type: "language",
        provider: "openai_compatible",
        name: "qwen3-next-80b-a3b-instruct",
      },
      { id: "g", type: "language", provider: "groq", name: "llama" },
    ];
    expect(
      pickProviderFallbackChain(models, CHAT_PROVIDER_ORDER, {
        profile: "fast",
      }).ids[0]
    ).toBe("fast");
    expect(
      pickProviderFallbackChain(models, CHAT_PROVIDER_ORDER, {
        profile: "deep",
      }).ids[0]
    ).toBe("deep");
  });

  it("races top-2 cloud and defers ollama", () => {
    const byId = {
      c: {
        id: "c",
        type: "language",
        provider: "openai_compatible",
        name: "gpt-oss-120b",
      },
      o: { id: "o", type: "language", provider: "openrouter", name: "free" },
      g: { id: "g", type: "language", provider: "groq", name: "g" },
      l: { id: "l", type: "language", provider: "ollama", name: "q" },
    };
    const { raceIds, restIds } = splitChatRaceChain(["c", "o", "g", "l"], byId);
    expect(raceIds).toEqual(["c", "o"]);
    expect(restIds).toEqual(["g", "l"]);
  });

  it("fast path races only the first healthy cloud model", () => {
    const byId = {
      c: {
        id: "c",
        type: "language",
        provider: "openai_compatible",
        name: "gpt-oss-120b",
      },
      o: { id: "o", type: "language", provider: "openrouter", name: "free" },
      l: { id: "l", type: "language", provider: "ollama", name: "q" },
    };
    const { raceIds, restIds } = splitChatRaceChain(["c", "o", "l"], byId, {
      fast: true,
    });
    expect(raceIds).toEqual(["c"]);
    expect(restIds).toEqual(["o", "l"]);
  });
});

describe("isSimpleNotebookChatQuery", () => {
  it("treats short questions and single-source scope as simple", () => {
    expect(isSimpleNotebookChatQuery("Tóm tắt ngắn")).toBe(true);
    expect(
      isSimpleNotebookChatQuery("Chi tiết hơn một chút", {
        selectedSourceIds: ["source:a"],
      })
    ).toBe(true);
  });

  it("keeps deep multi-source analysis off the fast path", () => {
    expect(
      isSimpleNotebookChatQuery("So sánh và phân tích toàn bộ nguồn", {
        selectedSourceIds: ["a", "b", "c"],
      })
    ).toBe(false);
  });
});

describe("shrinkNotebookChatContext fast", () => {
  it("keeps truncated full_text on fast scoped path (grounding)", () => {
    const out = shrinkNotebookChatContext(
      {
        sources: [
          {
            id: "source:a",
            full_text: "x".repeat(12_000),
            insights: [{ content: "tóm tắt ngắn" }],
          },
        ],
        notes: [],
      },
      { scoped: true, fast: true }
    );
    expect(out.sources[0].full_text.length).toBeGreaterThan(100);
    expect(out.sources[0].full_text.length).toBeLessThan(12_000);
    expect(out.sources[0].full_text).toContain("đã cắt");
    expect(out.sources[0].insights[0].content).toContain("tóm tắt");
  });
});

describe("buildNotebookContextConfig fast", () => {
  it("requests full content for scoped sources on fast path", () => {
    const cfg = buildNotebookContextConfig(
      [{ id: "source:a", title: "A" }],
      ["source:a"],
      { fast: true }
    );
    expect(cfg.sources["source:a"]).toBe("full content");
  });
});

describe("buildTextFragmentHref", () => {
  it("appends text fragment for highlightable quotes", () => {
    const href = buildTextFragmentHref(
      "https://example.com/a#old",
      "Australia coastal defence policy"
    );
    expect(href).toContain("https://example.com/a#:~:text=");
    expect(href).toContain("Australia");
  });
});

describe("transform model order / preferred id", () => {
  it("uses ShopAIKey → Groq → OpenRouter → Ollama preference", () => {
    expect(TRANSFORM_PROVIDER_ORDER).toEqual([
      "shopaikey",
      "groq",
      "openrouter",
      "ollama",
    ]);
  });

  it("never prefers ollama when cloud models exist", () => {
    const byId = {
      o: { id: "o", type: "language", provider: "ollama", name: "qwen2.5:1.5b" },
      r: { id: "r", type: "language", provider: "openrouter", name: "openrouter/free" },
      g: { id: "g", type: "language", provider: "groq", name: "llama" },
    };
    expect(pickTransformPreferredModelId(["o", "r", "g"], byId, "o")).toBe("r");
    expect(pickTransformPreferredModelId(["o", "r", "g"], byId, "")).toBe("r");
  });

  it("picks the first configured fallback when ShopAIKey is absent", () => {
    const list = [
      { id: "o", type: "language", provider: "ollama", name: "qwen2.5:1.5b" },
      { id: "paid", type: "language", provider: "openrouter", name: "anthropic/claude" },
      {
        id: "free",
        type: "language",
        provider: "openrouter",
        name: "openrouter/free",
      },
      { id: "g", type: "language", provider: "groq", name: "llama-3.3" },
      {
        id: "c",
        type: "language",
        provider: "openai_compatible",
        name: "gpt-oss-120b",
      },
    ];
    const { ids, providers } = pickTransformFallbackChain(list);
    expect(providers[0]).toBe("groq");
    expect(ids[0]).toBe("g");
    expect(providers[providers.length - 1]).toBe("ollama");
  });

  it("shortens max_tokens for Tóm tắt tình hình style presets", () => {
    expect(
      transformMaxTokensForPreset({
        name: "Simple Summary",
        title: "Tóm tắt tình hình",
      })
    ).toBe(1536);
    expect(
      transformMaxTokensForPreset({ name: "Translate Formal VN" })
    ).toBe(6144);
  });
});


describe("buildTransformModelTryOrder", () => {
  it("does not pin ollama ahead of cloud", () => {
    const byId = {
      o: { id: "o", type: "language", provider: "openrouter", name: "free" },
      g: { id: "g", type: "language", provider: "groq", name: "llama" },
      l: { id: "l", type: "language", provider: "ollama", name: "qwen2.5:1.5b" },
    };
    const order = buildTransformModelTryOrder(["o", "g", "l"], "l", byId);
    expect(order[0]).toBe("o");
    expect(order[order.length - 1]).toBe("l");
  });

  it("pins selected cloud model first", () => {
    const byId = {
      o: { id: "o", type: "language", provider: "openrouter", name: "free" },
      g: { id: "g", type: "language", provider: "groq", name: "llama" },
    };
    expect(buildTransformModelTryOrder(["o", "g"], "g", byId)[0]).toBe("g");
  });
});

describe("pickTransformPreferredModelId", () => {
  it("skips ollama when cloud exists", () => {
    const byId = {
      o: { id: "o", provider: "openrouter", type: "language", name: "x" },
      l: { id: "l", provider: "ollama", type: "language", name: "q" },
    };
    expect(pickTransformPreferredModelId(["l", "o"], byId, "l")).toBe("o");
  });
});

describe("transformMaxTokensForPreset", () => {
  it("shrinks summary presets", () => {
    expect(transformMaxTokensForPreset({ name: "Simple Summary" })).toBe(1536);
    expect(transformMaxTokensForPreset({ title: "Tóm tắt tình hình" })).toBe(1536);
    expect(transformMaxTokensForPreset({ name: "Translate Formal VN" })).toBe(6144);
  });
});

describe("splitTransformRaceChain", () => {
  it("races two distinct cloud providers and defers ollama", () => {
    const byId = {
      o: { id: "o", type: "language", provider: "openrouter", name: "free" },
      g: { id: "g", type: "language", provider: "groq", name: "llama" },
      c: {
        id: "c",
        type: "language",
        provider: "openai_compatible",
        name: "gpt-oss-120b",
      },
      l: { id: "l", type: "language", provider: "ollama", name: "q" },
    };
    const { raceIds, restIds } = splitTransformRaceChain(
      ["o", "g", "c", "l"],
      byId
    );
    expect(raceIds).toEqual(["o", "g"]);
    expect(restIds).toEqual(["c", "l"]);
  });
});

describe("Notebook output guard", () => {
  it("renders internal source ids as full readable references", () => {
    const result = formatNotebookCitations(
      "Oracle ký thỏa thuận với Bộ Quốc phòng Mỹ [source:abc].",
      {
        sources: [
          {
            id: "source:abc",
            title: "Oracle ký thỏa thuận phần mềm trị giá 7 tỷ USD",
            asset: { url: "https://example.com/oracle-deal" },
          },
        ],
      }
    );
    expect(result.text).not.toContain("source:abc");
    expect(result.text).toContain("[1]");
    expect(result.text).toContain("Nguồn tham khảo:");
    expect(result.text).toContain(
      "[Oracle ký thỏa thuận phần mềm trị giá 7 tỷ USD](https://example.com/oracle-deal)"
    );
  });

  it("numbers different sources and reuses the same number", () => {
    const result = formatNotebookCitations(
      "Dữ kiện A [source:a]. Dữ kiện B [source:b]. Dữ kiện A2 [source:a].",
      {
        sources: [
          { id: "source:a", title: "Nguồn A", url: "https://a.example" },
          { id: "source:b", title: "Nguồn B", url: "https://b.example" },
        ],
      }
    );
    expect(result.references.map((item) => item.number)).toEqual([1, 2]);
    expect(result.text.match(/\[1\]/g)?.length).toBeGreaterThanOrEqual(3);
    expect(result.text).not.toMatch(/\[source:/i);
  });

  it("fires the UI watchdog before 15 seconds", () => {
    expect(CHAT_HANG_TIMEOUT_MS).toBe(14_000);
  });

  it("rejects a long English answer from interactive rendering", () => {
    expect(
      notebookAnswerQualityIssue(
        "Oracle signed a seven billion dollar enterprise software agreement with the United States Department of Defense for a ten year contract period."
      )
    ).toBe("not_vietnamese");
  });

  it("removes leaked instructions and normalizes numbered answer fragments", () => {
    const draft =
      "Hệ thống nhắc nhở phải đưa ra 3-5 câu và không sử dụng markdown.\n\n" +
      "Câu 1: Starlink được triển khai thí điểm qua doanh nghiệp được cấp phép.\n\n" +
      "Câu 2: Thiết bị cũng được chào bán qua các kênh không chính thức.";
    const clean = sanitizeNotebookAnswer(draft);
    expect(clean).not.toContain("Hệ thống nhắc nhở");
    expect(clean).not.toContain("Câu 1:");
    expect(clean).toContain("Starlink được triển khai");
    expect(clean).toContain("Theo đó");
    expect(notebookAnswerQualityIssue(clean)).toBe(null);
  });

  it("flags unsanitized prompt leakage", () => {
    expect(
      notebookAnswerQualityIssue(
        "Hệ thống yêu cầu phải trả lời theo prompt nội bộ. Đây là phần trả lời có độ dài đầy đủ nhưng không được phép hiển thị."
      )
    ).toBe("prompt_leakage");
  });

  it("builds a cited administrative fallback from context only", () => {
    const answer = buildDeterministicGroundedAnswer(
      "thỏa thuận liên quan đến nước nào",
      {
        sources: [
          {
            id: "source:oracle",
            title: "Lầu Năm Góc ký thỏa thuận phần mềm",
            insights: [
              {
                content:
                  "Bộ Quốc phòng Mỹ đã ký thỏa thuận phần mềm trị giá 7 tỷ đô-la Mỹ. Hợp đồng được áp dụng cho Bộ Quốc phòng, Cảnh sát biển và Cộng đồng Tình báo.",
              },
            ],
          },
        ],
      }
    );
    expect(answer).toContain("Nguồn tin đề cập");
    expect(answer).toContain("Mỹ");
    expect(answer).toContain("[source:oracle]");
    expect(answer).not.toContain("provider");
  });
});
