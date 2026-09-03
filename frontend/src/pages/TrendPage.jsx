import { useEffect, useMemo, useRef, useState } from "react";
import { PROVIDERS, CJK, count, dateLabel, pendingTexts, readSaved, request, save, translation } from "../features/trends/feed";
import "../features/trends/trend.css";

function Vietnamese({ text, supplied, dictionary, name = false }) {
  const translated = translation(text, dictionary, supplied);
  if (translated) return translated;
  // Brand and account names retain their spelling; foreign prose never passes
  // as a finished Vietnamese translation.
  if (name && !CJK.test(text || "")) return text;
  return <span className="trend-translating">Đang dịch sang tiếng Việt…</span>;
}

function RankingCard({ board, state, dictionary, starred, toggleStar, refresh, minimal }) {
  const source = board.id.replace("newsnow:", "");
  return (
    <section className={`trend-card ${minimal ? "trend-card-minimal" : ""}`} style={{ "--accent": board.accent || "#689bd9" }} aria-label={board.name}>
      <header className="trend-card-header">
        {board.icon ? <img className="trend-source-icon" src={board.icon} alt="" loading="lazy" /> : <span className="trend-source-icon">{board.name.slice(0, 2).toUpperCase()}</span>}
        <div className="trend-card-heading">
          <h2><Vietnamese text={board.name} supplied={board.name_vi} dictionary={dictionary} name /></h2>
          <p><Vietnamese text={board.subtitle} supplied={board.subtitle_vi} dictionary={dictionary} />{state?.stale ? " · Bản lưu gần nhất" : ""}</p>
        </div>
        {!minimal && <>
          <button className="trend-icon-button" aria-label={`Làm mới ${board.name}`} disabled={state?.loading} onClick={() => refresh(source)}>↻</button>
          <button className={`trend-icon-button ${starred ? "is-starred" : ""}`} aria-label={`${starred ? "Bỏ" : "Thêm"} theo dõi ${board.name}`} aria-pressed={starred} onClick={() => toggleStar(board.id)}>{starred ? "★" : "☆"}</button>
        </>}
      </header>
      <div className="trend-ranking-scroll" tabIndex={0} aria-label={`Danh sách ${board.name}`}>
        {board.items.map((item) => (
          <a className="trend-ranking-row" key={item.id} href={item.url || board.url} target="_blank" rel="noreferrer">
            <span className={`trend-rank ${item.rank <= 3 ? "trend-rank-top" : ""}`}>{item.rank}</span>
            <span><Vietnamese text={item.title} supplied={item.title_vi} dictionary={dictionary} /></span>
          </a>
        ))}
        {!board.items.length && <div className="trend-board-message" role="status">
          {state?.loading ? <><span className="trend-skeleton" /><span className="trend-skeleton" /><span className="trend-skeleton" />Đang tải bảng…</> : state?.error || "Nguồn chưa có mục mới."}
          {!state?.loading && <button className="trend-text-button" onClick={() => refresh(source)}>Thử lại</button>}
        </div>}
      </div>
      {state?.error && board.items.length > 0 && <p className="trend-card-note">Giữ bản gần nhất · Chưa kết nối lại được nguồn</p>}
    </section>
  );
}

function TweetCard({ item, dictionary }) {
  const [expanded, setExpanded] = useState(false);
  const metrics = item.metrics || {};
  return (
    <article className="trend-tweet">
      <div className="trend-tweet-avatar">
        {item.avatar ? <img src={item.avatar} alt="" loading="lazy" /> : <span>X</span>}
        <small>{count(metrics.followers)} người theo dõi</small>
      </div>
      <div className="trend-tweet-content">
        <header><a href={item.url} target="_blank" rel="noreferrer">{item.handle ? `@${item.handle}` : "Tác giả trên X"}</a><time>{dateLabel(item.published_at)}</time></header>
        <p className={expanded ? "" : "trend-tweet-preview"}><Vietnamese text={item.title} supplied={item.title_vi} dictionary={dictionary} /></p>
        {item.title.length > 200 && <button className="trend-text-button" onClick={() => setExpanded(!expanded)}>{expanded ? "Thu gọn" : "Xem đầy đủ"}</button>}
        <div className="trend-tweet-metrics"><span>♡ {count(metrics.likes)} lượt thích</span><span>↻ {count(metrics.reposts)} chia sẻ</span><span>♧ {count(metrics.comments)} bình luận</span><span>▥ {count(metrics.views)} lượt xem</span></div>
      </div>
      <aside className="trend-tweet-insights">
        <div><span>Khả năng lan truyền</span><strong>{metrics.probability == null ? "—" : `${metrics.probability}%`}</strong></div>
        <div><span>Lượt xem dự báo</span><strong>{count(metrics.predicted_views)}</strong></div>
        <a href={item.url} target="_blank" rel="noreferrer">Mở bài đăng trên X ↗</a>
      </aside>
    </article>
  );
}

export default function TrendPage() {
  const [provider, setProvider] = useState("newsnow");
  const [mode, setMode] = useState("hot");
  const [channel, setChannel] = useState("all");
  const [catalog, setCatalog] = useState(() => readSaved("catalog", null));
  const [states, setStates] = useState({});
  const [catalogError, setCatalogError] = useState("");
  const [dictionary, setDictionary] = useState(() => readSaved("translations", {}));
  const dictionaryRef = useRef(dictionary);
  const [translationBusy, setTranslationBusy] = useState(false);
  const [translationRetry, setTranslationRetry] = useState(0);
  const [revision, setRevision] = useState(0);
  const [limit, setLimit] = useState(9);
  const [stars, setStars] = useState(() => readSaved("stars", []));
  const attempts = useRef(new Map());
  const mounted = useRef(true);
  const active = useRef(new Map());

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    request("catalog/", { signal: controller.signal }).then((data) => { setCatalog(data); save("catalog", data); setCatalogError(""); }).catch((err) => { if (!controller.signal.aborted) setCatalogError(err.status ? err.message : "Chưa tải được danh sách nguồn. Hãy thử lại."); });
    return () => { mounted.current = false; controller.abort(); active.current.forEach((c) => c.abort()); active.current.clear(); };
  }, [revision]);

  const selectedSources = useMemo(() => {
    if (provider !== "newsnow") return [{ id: provider === "rebang" ? channel : "all" }];
    return (catalog?.newsnow || []).filter((source) => mode === "follow" ? stars.includes(`newsnow:${source.id}`) : source.mode === mode).slice(0, limit);
  }, [catalog, channel, limit, mode, provider, stars]);
  const sourceKey = selectedSources.map((source) => source.id).join(",");

  async function loadSource(source, targetProvider = provider) {
    const key = `${targetProvider}:${source}`;
    if (active.current.has(key)) return;
    const controller = new AbortController();
    active.current.set(key, controller);
    const saved = readSaved(key, null);
    setStates((prev) => ({ ...prev, [key]: { ...(prev[key] || saved), loading: true } }));
    try {
      const data = await request(`boards/?provider=${targetProvider}&source=${encodeURIComponent(source)}`, { signal: controller.signal });
      if (controller.signal.aborted || !mounted.current) return;
      const value = { ...data, loading: false };
      setStates((prev) => ({ ...prev, [key]: value }));
      save(key, value);
    } catch (err) {
      if (!controller.signal.aborted && mounted.current) setStates((prev) => ({ ...prev, [key]: { ...(prev[key] || saved), loading: false, stale: true, error: err.status ? err.message : "Nguồn phản hồi chậm. Bảng sẽ tự thử lại." } }));
    } finally { if (active.current.get(key) === controller) active.current.delete(key); }
  }

  useEffect(() => {
    let cancelled = false;
    const queue = sourceKey ? sourceKey.split(",") : [];
    async function worker() {
      while (queue.length && !cancelled) {
        const source = queue.shift();
        await loadSource(source, provider);
      }
    }
    Promise.all([worker(), worker(), worker()]);
    return () => { cancelled = true; };
    // Sources update progressively, never waiting for all platforms to finish.
  }, [sourceKey, provider, revision]);

  const boards = useMemo(() => selectedSources.flatMap((source) => {
    const state = states[`${provider}:${source.id}`];
    if (state?.boards?.length) return state.boards.map((board) => ({ ...board, state, requestSource: source.id }));
    if (provider !== "newsnow") return [];
    return [{ ...source, id: `newsnow:${source.id}`, provider, items: [], state: state || { loading: true }, requestSource: source.id }];
  }), [provider, selectedSources, states]);
  const texts = useMemo(() => pendingTexts(boards, dictionary), [boards, dictionary]);
  const textsKey = texts.join("\u0000");

  useEffect(() => {
    if (!texts.length) { setTranslationBusy(false); return; }
    let cancelled = false;
    const controller = new AbortController();
    let timer;
    async function translate() {
      setTranslationBusy(true);
      const queue = texts.filter((text) => !dictionaryRef.current[text] && (attempts.current.get(text)?.retryAt || 0) <= Date.now());
      if (queue.length && !cancelled) {
        const batch = [];
        let size = 0;
        while (queue.length && batch.length < 16 && size + queue[0].length <= 4000) { const text = queue.shift(); size += text.length; batch.push(text); }
        if (!batch.length) return;
        try {
          const data = await request("translate/", { method: "post", body: { texts: batch }, signal: controller.signal });
          if (cancelled) return;
          for (const row of data.items || []) if (row.status === "ok" && row.vi && !CJK.test(row.vi)) dictionaryRef.current[row.text] = row.vi;
        } catch { if (cancelled) return; }
        for (const text of batch) if (!dictionaryRef.current[text]) {
          const n = (attempts.current.get(text)?.count || 0) + 1;
          attempts.current.set(text, { count: n, retryAt: Date.now() + Math.min(300000, 30000 * 2 ** (n - 1)) });
        }
        setDictionary({ ...dictionaryRef.current });
        save("translations", Object.fromEntries(Object.entries(dictionaryRef.current).slice(-5000)));
      }
      if (!cancelled) {
        setTranslationBusy(false);
        timer = setTimeout(() => setTranslationRetry((n) => n + 1), queue.length ? 100 : 30000);
      }
    }
    translate();
    return () => { cancelled = true; controller.abort(); clearTimeout(timer); };
  }, [textsKey, translationRetry]);

  useEffect(() => {
    const timer = setInterval(() => { if (document.visibilityState === "visible") setRevision((n) => n + 1); }, 180000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    const timer = setInterval(() => {
      if (document.visibilityState !== "visible") return;
      selectedSources.filter((source) => states[`${provider}:${source.id}`]?.error).slice(0, 3).forEach((source) => loadSource(source.id));
    }, 30000);
    return () => clearInterval(timer);
  }, [sourceKey, provider, states]);
  const providerState = states[`${provider}:${provider === "rebang" ? channel : "all"}`];
  const isLoading = boards.some((board) => board.state?.loading) || (provider !== "newsnow" && (!providerState || providerState.loading));
  const totalSources = (catalog?.newsnow || []).filter((source) => mode === "follow" ? stars.includes(`newsnow:${source.id}`) : source.mode === mode).length;
  const toggleStar = (id) => setStars((prev) => { const next = prev.includes(id) ? prev.filter((value) => value !== id) : [...prev, id]; save("stars", next); return next; });
  const refresh = () => { attempts.current.clear(); setTranslationRetry((n) => n + 1); setRevision((n) => n + 1); };

  return (
    <div className={`trend-page trend-${provider}`}>
      <div className="trend-platform-bar">
        <h1>Xu hướng</h1>
        <nav aria-label="Nền tảng xu hướng">{PROVIDERS.map((entry) => <button key={entry.id} className={provider === entry.id ? "active" : ""} aria-pressed={provider === entry.id} onClick={() => { setProvider(entry.id); setLimit(9); }}>{entry.label}</button>)}</nav>
        <button className="trend-refresh" onClick={refresh} aria-label="Làm mới bảng xu hướng">↻ <span>Làm mới</span></button>
      </div>
      <div className="trend-hero">
        <div className="trend-brand">{provider === "newsnow" ? <><b>News</b><b>Now<span> · Tiếng Việt</span></b></> : provider === "rebang" ? "REBANG" : "Bài đăng đang lan truyền trên X"}</div>
        <p>{provider === "newsnow" ? "Các bảng xếp hạng, cập nhật từ NewsNow" : provider === "rebang" ? "Bảng xếp hạng tối giản · Tiếng Việt" : "Nội dung nổi bật và số liệu tương tác từ SoPilot"}</p>
        <div className="trend-mode-nav">
          {provider === "newsnow" && [["hot", "Nóng nhất"], ["latest", "Thời gian thực"], ["follow", "Đang theo dõi"]].map(([key, label]) => <button key={key} className={mode === key ? "active" : ""} onClick={() => { setMode(key); setLimit(9); }}>{label}</button>)}
          {provider === "rebang" && (catalog?.rebang || []).map((entry) => <button key={entry.id} className={channel === entry.id ? "active" : ""} onClick={() => setChannel(entry.id)}>{entry.name}</button>)}
        </div>
      </div>
      <div className="trend-status-line" role="status">
        <span>{isLoading ? "Đang cập nhật các nguồn…" : `${boards.reduce((n, board) => n + board.items.length, 0)} mục · Giữ thứ tự xếp hạng của nguồn`}</span>
        <span>{texts.length ? translationBusy ? "Đang dịch sang tiếng Việt…" : "Đang chờ dịch các mục còn lại · tự thử lại" : "Tiếng Việt"}</span>
      </div>
      {catalogError && <p className="trend-notice" role="alert">{catalogError}<button onClick={refresh}>Thử lại</button></p>}
      {provider !== "newsnow" && providerState?.error && <p className="trend-notice" role="alert">{providerState.error}<button onClick={refresh}>Thử lại</button></p>}
      {provider === "sopilot" ? <div className="trend-tweet-list">{boards.flatMap((board) => board.items).map((item) => <TweetCard key={item.id} item={item} dictionary={dictionary} />)}</div> : <div className="trend-grid">{boards.map((board) => <RankingCard key={board.id} board={board} state={board.state} dictionary={dictionary} minimal={provider === "rebang"} starred={stars.includes(board.id)} toggleStar={toggleStar} refresh={() => loadSource(board.requestSource)} />)}</div>}
      {!boards.length && <div className="trend-board-message">{provider === "newsnow" && mode === "follow" ? "Nhấn ☆ trên một bảng để thêm vào danh sách theo dõi." : isLoading ? "Đang lấy bảng từ nguồn…" : "Chưa tải được bảng. Các nguồn sẽ được thử lại tự động."}</div>}
      {provider === "newsnow" && totalSources > limit && <button className="trend-load-more" onClick={() => setLimit((n) => n + 9)}>Hiển thị thêm bảng ({totalSources - limit})</button>}
      <footer className="trend-footer">Nguồn: <a href={PROVIDERS.find((entry) => entry.id === provider).url} target="_blank" rel="noreferrer">{PROVIDERS.find((entry) => entry.id === provider).label} ↗</a> · Nội dung được dịch tự động sang tiếng Việt.</footer>
    </div>
  );
}
