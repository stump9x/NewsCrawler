import { useEffect, useMemo, useRef, useState } from "react";
import { CJK, pendingTexts, readSaved, request, save, translation } from "../features/trends/feed";
import { createTranslationQueue } from "../features/trends/translationQueue";
import "../features/trends/trend.css";

function Vietnamese({ text, supplied, dictionary, name = false }) {
  const translated = translation(text, dictionary, supplied);
  if (translated) return translated;
  // Brand and account names retain their spelling; foreign prose never passes
  // as a finished Vietnamese translation.
  if (name && !CJK.test(text || "")) return text;
  return <span className="trend-translating">Đang dịch sang tiếng Việt…</span>;
}

function RankingCard({ board, state, dictionary, refresh }) {
  return (
    <section className="trend-card" style={{ "--accent": board.accent || "#689bd9" }} aria-label={board.name}>
      <header className="trend-card-header">
        {board.icon ? <img className="trend-source-icon" src={board.icon} alt="" loading="lazy" /> : <span className="trend-source-icon">{board.name.slice(0, 2).toUpperCase()}</span>}
        <div className="trend-card-heading">
          <h2><Vietnamese text={board.name} supplied={board.name_vi} dictionary={dictionary} name /></h2>
          <p><Vietnamese text={board.subtitle} supplied={board.subtitle_vi} dictionary={dictionary} />{state?.stale ? " · Bản lưu gần nhất" : ""}</p>
        </div>
        <button className="trend-icon-button" aria-label={`Làm mới ${board.name}`} disabled={state?.loading} onClick={refresh}>↻</button>
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
          {!state?.loading && <button className="trend-text-button" onClick={refresh}>Thử lại</button>}
        </div>}
      </div>
      {state?.error && board.items.length > 0 && <p className="trend-card-note">Giữ bản gần nhất · Chưa kết nối lại được nguồn</p>}
    </section>
  );
}

export default function TrendPage() {
  const [catalog, setCatalog] = useState(() => readSaved("catalog:compact", null));
  const [states, setStates] = useState({});
  const [catalogError, setCatalogError] = useState("");
  const [dictionary, setDictionary] = useState(() => readSaved("translations", {}));
  const dictionaryRef = useRef(dictionary);
  const translationQueue = useRef(null);
  const [revision, setRevision] = useState(0);
  const mounted = useRef(true);
  const active = useRef(new Map());

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    request("catalog/", { signal: controller.signal }).then((data) => { setCatalog(data); save("catalog:compact", data); setCatalogError(""); }).catch((err) => { if (!controller.signal.aborted) setCatalogError(err.status ? err.message : "Chưa tải được danh sách nguồn. Hãy thử lại."); });
    return () => { mounted.current = false; controller.abort(); active.current.forEach((c) => c.abort()); active.current.clear(); };
  }, [revision]);

  const selectedSources = useMemo(() => catalog?.newsnow || [], [catalog]);
  const sourceKey = selectedSources.map((source) => source.id).join(",");

  async function loadSource(source) {
    const key = `newsnow:${source}`;
    if (active.current.has(key)) return;
    const controller = new AbortController();
    active.current.set(key, controller);
    const saved = readSaved(key, null);
    const previous = states[key] || saved;
    setStates((prev) => ({ ...prev, [key]: { ...(prev[key] || saved), loading: true } }));
    try {
      const data = await request(`boards/?provider=newsnow&source=${encodeURIComponent(source)}`, { signal: controller.signal });
      if (controller.signal.aborted || !mounted.current) return;
      const value = !data.boards?.some((board) => board.items?.length) && previous?.boards?.some((board) => board.items?.length)
        ? { ...previous, loading: false, stale: true, error: "Nguồn chưa có bản mới. Đang giữ bản gần nhất." }
        : { ...data, loading: false };
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
        await loadSource(source);
      }
    }
    Promise.all([worker(), worker(), worker()]);
    return () => { cancelled = true; };
    // Sources update progressively, never waiting for all platforms to finish.
  }, [sourceKey, revision]);

  const boards = useMemo(() => selectedSources.flatMap((source) => {
    const requestSource = source.id;
    const state = states[`newsnow:${requestSource}`];
    if (state?.boards?.length) return state.boards.map((board) => ({ ...board, ...(source.name ? { id: `newsnow:${source.id}`, name: source.name, name_vi: source.name, subtitle: source.subtitle, subtitle_vi: source.subtitle, accent: source.accent } : {}), state, requestSource }));
    return [{ ...source, subtitle_vi: source.subtitle, id: `newsnow:${source.id}`, items: [], state: state || { loading: true }, requestSource }];
  }), [selectedSources, states]);
  const texts = useMemo(() => pendingTexts(boards, dictionary), [boards, dictionary]);
  const textsKey = texts.join("\u0000");

  useEffect(() => {
    const queue = createTranslationQueue({
      translate: (batch, signal) => request("translate/", { method: "post", body: { texts: batch }, signal }),
      onResult: (rows) => {
        Object.assign(dictionaryRef.current, rows);
        setDictionary({ ...dictionaryRef.current });
        save("translations", Object.fromEntries(Object.entries(dictionaryRef.current).slice(-5000)));
      },
    });
    translationQueue.current = queue;
    return () => { queue.stop(); translationQueue.current = null; };
  }, []);
  useEffect(() => { translationQueue.current?.update(texts); }, [textsKey]);
  useEffect(() => {
    const resume = () => { if (document.visibilityState === "visible") translationQueue.current?.resume(); };
    window.addEventListener("online", resume);
    document.addEventListener("visibilitychange", resume);
    return () => {
      window.removeEventListener("online", resume);
      document.removeEventListener("visibilitychange", resume);
    };
  }, []);

  useEffect(() => {
    const timer = setInterval(() => { if (document.visibilityState === "visible") setRevision((n) => n + 1); }, 180000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    const timer = setInterval(() => {
      if (document.visibilityState !== "visible") return;
      sourceKey.split(",").filter((source) => states[`newsnow:${source}`]?.error).slice(0, 3).forEach((source) => loadSource(source));
    }, 30000);
    return () => clearInterval(timer);
  }, [sourceKey, states]);
  const isLoading = boards.some((board) => board.state?.loading) || (!catalog && !catalogError);
  const refresh = () => { translationQueue.current?.retry(); setRevision((n) => n + 1); };

  return (
    <div className="trend-page trend-newsnow">
      <div className="trend-platform-bar">
        <h1>Xu hướng</h1>
        <button className="trend-refresh" onClick={refresh} aria-label="Làm mới bảng xu hướng">↻ <span>Làm mới</span></button>
      </div>
      <div className="trend-hero">
        <p>Tin tức tổng hợp từ các nền tảng</p>
      </div>
      {catalogError && <p className="trend-notice" role="alert">{catalogError}<button onClick={refresh}>Thử lại</button></p>}
      <div className="trend-grid">{boards.map((board) => <RankingCard key={board.id} board={board} state={board.state} dictionary={dictionary} refresh={() => loadSource(board.requestSource)} />)}</div>
      {!boards.length && <div className="trend-board-message">{isLoading ? "Đang lấy bảng từ nguồn…" : "Chưa tải được bảng. Các nguồn sẽ được thử lại tự động."}</div>}
    </div>
  );
}
