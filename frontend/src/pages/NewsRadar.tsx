import { useCallback, useEffect, useMemo, useState } from "react";
import { Newspaper, RefreshCw, ExternalLink, Search, Radar } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type NewsArticle } from "@/lib/api";
import { AiInsightPanel } from "@/components/common/AiInsightPanel";

/** Soft topic chips — only nudge the Eastmoney keyword, never hardcode tickers. */
const TOPIC_PRESETS = [
  { id: "", label: "最新财经" },
  { id: "global", label: "宏观经济" },
  { id: "ai", label: "人工智能" },
  { id: "crypto", label: "加密货币" },
  { id: "robot", label: "人形机器人" },
  { id: "semiconductor", label: "半导体" },
  { id: "newenergy", label: "新能源" },
] as const;

function signalColor(signal?: string | null): string {
  if (signal === "positive") return "bg-rose-500";
  if (signal === "negative") return "bg-emerald-500";
  return "bg-muted-foreground/40";
}

function signalLabel(signal?: string | null): string {
  if (signal === "positive") return "偏多";
  if (signal === "negative") return "偏空";
  return "中性";
}

/** Sina ctime / ISO / plain text → localized display. */
function formatPublished(value?: string | null): string {
  if (!value?.trim()) return "—";
  const raw = value.trim();
  if (/^\d{10,13}$/.test(raw)) {
    const ts = raw.length >= 13 ? Number(raw) / 1000 : Number(raw);
    const d = new Date(ts * 1000);
    if (!Number.isNaN(d.getTime())) {
      return d.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
    }
  }
  const parsed = Date.parse(raw);
  if (!Number.isNaN(parsed)) {
    return new Date(parsed).toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  }
  return raw.length > 16 ? raw.slice(0, 16) : raw;
}

export function NewsRadar() {
  const [topic, setTopic] = useState<string>("");
  const [query, setQuery] = useState("");
  const [input, setInput] = useState("");
  const [articles, setArticles] = useState<NewsArticle[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string | number> = { limit: 20 };
      if (query) params.q = query;
      else if (topic) params.topic = topic;
      const res = await api.fetchNewsRadar(params);
      setArticles(res.articles || []);
      setNotes(res.source_notes || []);
    } catch (e) {
      setArticles([]);
      setNotes([e instanceof Error ? e.message : "新闻获取失败"]);
    } finally {
      setUpdatedAt(new Date());
      setLoading(false);
    }
  }, [topic, query]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const id = window.setInterval(() => void load(), 25_000);
    return () => window.clearInterval(id);
  }, [load]);

  const stamp = useMemo(
    () => (updatedAt ? updatedAt.toLocaleString("zh-CN", { hour12: false }) : "—"),
    [updatedAt],
  );

  const positive = articles.filter((a) => a.signal === "positive").length;
  const negative = articles.filter((a) => a.signal === "negative").length;

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-5 p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Radar className="h-6 w-6 text-primary" />
            <h1 className="text-2xl font-bold">新闻雷达</h1>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            新浪财经 / Tushare / 东方财富 多源并行 · 秒级返回 · 25s 自动刷新
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm text-muted-foreground hover:bg-muted disabled:opacity-60"
        >
          <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          刷新
        </button>
      </div>

      <div className="flex flex-col gap-3 rounded-xl border bg-card p-4">
        <div className="flex flex-wrap gap-2">
          {TOPIC_PRESETS.map((t) => (
            <button
              key={t.id || "latest"}
              type="button"
              onClick={() => {
                setTopic(t.id);
                setQuery("");
                setInput("");
              }}
              className={cn(
                "rounded-full px-3 py-1 text-xs transition",
                topic === t.id && !query
                  ? "bg-primary text-primary-foreground"
                  : "border text-muted-foreground hover:bg-muted",
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const v = input.trim();
            setQuery(v);
            setTopic("");
          }}
        >
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute start-3 top-2.5 h-4 w-4 text-muted-foreground" />
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="搜索关键词或代码，如 算力 / 600519.SH / 固态电池"
              className="w-full rounded-md border bg-background px-9 py-2 text-sm outline-none focus:border-primary"
            />
          </div>
          <button
            type="submit"
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
          >
            扫描
          </button>
        </form>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg border bg-card px-3 py-2">
          <div className="text-xs text-muted-foreground">条目</div>
          <div className="text-xl font-semibold tabular-nums">{articles.length}</div>
        </div>
        <div className="rounded-lg border bg-card px-3 py-2">
          <div className="text-xs text-muted-foreground">偏多信号</div>
          <div className="text-xl font-semibold tabular-nums text-rose-500">{positive}</div>
        </div>
        <div className="rounded-lg border bg-card px-3 py-2">
          <div className="text-xs text-muted-foreground">偏空信号</div>
          <div className="text-xl font-semibold tabular-nums text-emerald-500">{negative}</div>
        </div>
      </div>

      {notes.length > 0 ? (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-600 dark:text-amber-300">
          {notes.join(" · ")}
        </div>
      ) : null}

      <section className="flex flex-col gap-2">
        {loading && articles.length === 0 ? (
          <div className="space-y-2">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="h-20 animate-pulse rounded-xl bg-muted/40" />
            ))}
          </div>
        ) : articles.length === 0 ? (
          <div className="rounded-xl border bg-card px-4 py-10 text-center text-sm text-muted-foreground">
            <Newspaper className="mx-auto mb-2 h-8 w-8 opacity-40" />
            暂无新闻。可换关键词或稍后重试。
          </div>
        ) : (
          articles.map((item, idx) => (
            <article
              key={`${item.title}-${idx}`}
              className="rounded-xl border bg-card px-4 py-3 shadow-sm transition hover:border-primary/40"
            >
              <div className="flex items-start gap-3">
                <span
                  className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", signalColor(item.signal))}
                  title={signalLabel(item.signal)}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    {item.topic ? (
                      <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                        {item.topic}
                      </span>
                    ) : null}
                    <span className="text-[11px] text-muted-foreground">{item.source || "—"}</span>
                    <span className="text-[11px] text-muted-foreground">
                      {formatPublished(item.published)}
                    </span>
                    <span className="text-[10px] text-muted-foreground/80">
                      {signalLabel(item.signal)}
                    </span>
                  </div>
                  <h2 className="mt-1 text-sm font-semibold leading-snug">
                    {item.url ? (
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noreferrer"
                        className="hover:text-primary hover:underline"
                      >
                        {item.title}
                        <ExternalLink className="ms-1 inline h-3 w-3 opacity-50" />
                      </a>
                    ) : (
                      item.title
                    )}
                  </h2>
                  {item.snippet ? (
                    <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                      {item.snippet}
                    </p>
                  ) : null}
                </div>
              </div>
            </article>
          ))
        )}
      </section>

      <AiInsightPanel
        kind="news"
        title="AI 资讯扫描"
        autoRun={false}
        payload={{ articles }}
      />

      <p className="text-center text-[11px] text-muted-foreground">
        更新：{stamp} · 东方财富公开资讯 · AI 扫描依赖已配置的 LLM · 不构成投资建议
      </p>
    </div>
  );
}
