import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type SentimentItem } from "@/lib/api";
import { AiInsightPanel } from "@/components/common/AiInsightPanel";

function heat(probability: number): { label: string; color: string; bar: string } {
  if (probability >= 70) {
    return { label: "过热", color: "text-rose-400", bar: "bg-rose-500" };
  }
  if (probability >= 55) {
    return { label: "偏热", color: "text-orange-400", bar: "bg-orange-500" };
  }
  if (probability >= 40) {
    return { label: "中性", color: "text-amber-300", bar: "bg-amber-400" };
  }
  if (probability >= 25) {
    return { label: "偏冷", color: "text-sky-400", bar: "bg-sky-500" };
  }
  return { label: "冰冷", color: "text-cyan-300", bar: "bg-cyan-400" };
}

function Gauge({ value }: { value: number }) {
  const clamped = Math.max(0, Math.min(100, value));
  const angle = -90 + (clamped / 100) * 180;
  const h = heat(clamped);
  return (
    <div className="flex flex-col items-center">
      <div className="relative h-36 w-56">
        <svg viewBox="0 0 200 110" className="h-full w-full">
          <path
            d="M20 100 A80 80 0 0 1 180 100"
            fill="none"
            stroke="hsl(var(--muted))"
            strokeWidth="14"
            strokeLinecap="round"
          />
          <path
            d="M20 100 A80 80 0 0 1 180 100"
            fill="none"
            stroke="url(#thermo)"
            strokeWidth="14"
            strokeLinecap="round"
            strokeDasharray={`${(clamped / 100) * 251} 251`}
          />
          <defs>
            <linearGradient id="thermo" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%" stopColor="#22d3ee" />
              <stop offset="50%" stopColor="#fbbf24" />
              <stop offset="100%" stopColor="#f43f5e" />
            </linearGradient>
          </defs>
          <line
            x1="100"
            y1="100"
            x2={100 + 70 * Math.cos((angle * Math.PI) / 180)}
            y2={100 + 70 * Math.sin((angle * Math.PI) / 180)}
            stroke="hsl(var(--foreground))"
            strokeWidth="2.5"
            strokeLinecap="round"
          />
          <circle cx="100" cy="100" r="5" fill="hsl(var(--foreground))" />
        </svg>
        <div className="absolute inset-x-0 bottom-0 text-center">
          <div className={cn("text-3xl font-bold tabular-nums", h.color)}>
            {clamped.toFixed(0)}
          </div>
          <div className="text-xs text-muted-foreground">综合预期温度 · {h.label}</div>
        </div>
      </div>
    </div>
  );
}

export function Sentiment() {
  const [items, setItems] = useState<SentimentItem[]>([]);
  const [composite, setComposite] = useState(50);
  const [source, setSource] = useState("polymarket");
  const [mode, setMode] = useState<string>("prediction");
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const res = await api.fetchSentiment();
      setItems(res.items || []);
      setComposite(res.composite ?? 50);
      setSource(res.source || "polymarket");
      setMode(res.mode || "prediction");
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "情绪数据获取失败");
    } finally {
      setUpdatedAt(new Date());
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const id = window.setInterval(() => void load(), 120_000);
    return () => window.clearInterval(id);
  }, [load]);

  const stamp = useMemo(
    () => (updatedAt ? updatedAt.toLocaleString("zh-CN", { hour12: false }) : "—"),
    [updatedAt],
  );

  const insightPayload = useMemo(
    () => ({
      composite,
      items: items
        .filter((i) => !i.error && i.probability > 0)
        .map((i) => ({
          title: i.title,
          probability: i.probability,
          delta24h: i.delta24h,
          source: i.source,
          category: i.category,
        })),
    }),
    [composite, items],
  );

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6 p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Activity className="h-6 w-6 text-primary" />
            <h1 className="text-2xl font-bold">情绪温度计</h1>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            {mode === "market_proxy"
              ? "东财热点板块 / 指数代理 · 预测市场不可达时自动切换 · 5 分钟缓存"
              : mode === "hybrid"
                ? "Polymarket 可用项 + A 股行情代理补全 · 5 分钟缓存"
                : "Polymarket / Kalshi 公开预测市场 · 宏观预期概率 · 5 分钟缓存"}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={refreshing}
          className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm text-muted-foreground hover:bg-muted disabled:opacity-60"
        >
          <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />
          刷新
        </button>
      </div>

      {error ? (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-600 dark:text-amber-300">
          {error}
        </div>
      ) : null}

      {mode === "market_proxy" ? (
        <div className="rounded-md border border-sky-500/25 bg-sky-500/5 px-3 py-2 text-xs text-sky-700 dark:text-sky-300">
          当前网络无法访问 Polymarket/Kalshi，已改用 A 股热点板块涨跌幅作为宏观情绪代理（非真实预测市场报价）。
        </div>
      ) : null}

      {mode === "hybrid" ? (
        <div className="rounded-md border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs text-muted-foreground">
          部分主题已用 A 股行情代理补全。
        </div>
      ) : null}

      <section className="rounded-2xl border bg-card p-6 shadow-sm">
        <div className="grid items-center gap-6 md:grid-cols-[280px_1fr]">
          <Gauge value={composite} />
          <div className="space-y-3">
            <h2 className="text-sm font-semibold">解读</h2>
            <p className="text-sm leading-relaxed text-muted-foreground">
              {mode === "market_proxy"
                ? "综合温度由六项代理指标加权：指数/板块强弱映射到降息、衰退、政策、AI、能源与风险偏好等维度。数值越高代表短线市场风险偏好越强。"
                : "综合温度由多项宏观预期概率加权得到：降息、衰退（反向）、中国政策、AI 资本开支、原油与加密等。数值越高代表市场对「风险资产友好」情景定价越充分。"}
            </p>
            <div className="flex flex-wrap gap-2 text-[11px]">
              <span className="rounded-full bg-sky-500/15 px-2 py-1 text-sky-300">冰冷 0–24</span>
              <span className="rounded-full bg-cyan-500/15 px-2 py-1 text-cyan-300">偏冷 25–39</span>
              <span className="rounded-full bg-amber-500/15 px-2 py-1 text-amber-300">中性 40–54</span>
              <span className="rounded-full bg-orange-500/15 px-2 py-1 text-orange-300">偏热 55–69</span>
              <span className="rounded-full bg-rose-500/15 px-2 py-1 text-rose-300">过热 70–100</span>
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-3 md:grid-cols-2">
        {refreshing && items.length === 0
          ? [1, 2, 3, 4].map((i) => (
              <div key={i} className="h-32 animate-pulse rounded-xl bg-muted/40" />
            ))
          : items.map((item) => {
              const ok = !item.error && item.probability > 0;
              const h = heat(ok ? item.probability : 50);
              const up = item.delta24h >= 0;
              return (
                <article key={item.id} className="rounded-xl border bg-card p-4 shadow-sm">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <div className="text-sm font-semibold leading-snug">{item.title}</div>
                      <div className="mt-1 text-[11px] text-muted-foreground">
                        {item.source} · {item.category} · {item.horizon}
                      </div>
                    </div>
                    <span className="shrink-0 rounded-full border px-2 py-0.5 text-[10px] text-muted-foreground">
                      {item.source}
                    </span>
                  </div>
                  {ok ? (
                    <>
                      <div className="mt-3 flex items-end justify-between">
                        <div className={cn("text-3xl font-bold tabular-nums", h.color)}>
                          {item.probability.toFixed(1)}
                          <span className="text-sm font-medium text-muted-foreground">%</span>
                        </div>
                        <div
                          className={cn(
                            "text-xs tabular-nums",
                            up ? "text-rose-400" : "text-emerald-400",
                          )}
                        >
                          24h {up ? "+" : ""}
                          {item.delta24h.toFixed(1)} pp
                        </div>
                      </div>
                      <div className="mt-3 h-2 overflow-hidden rounded-full bg-muted">
                        <div
                          className={cn("h-full rounded-full transition-all", h.bar)}
                          style={{ width: `${item.probability}%` }}
                        />
                      </div>
                    </>
                  ) : (
                    <p className="mt-3 text-xs text-muted-foreground">
                      {item.error || "暂无报价"}
                    </p>
                  )}
                  {item.note ? (
                    <p className="mt-2 text-[11px] text-muted-foreground">{item.note}</p>
                  ) : null}
                </article>
              );
            })}
      </section>

      <AiInsightPanel
        kind="sentiment"
        title="AI 情绪解读"
        autoRun={false}
        payload={insightPayload}
      />

      <p className="text-center text-[11px] text-muted-foreground">
        更新：{stamp} · 数据源：{source}
        {mode === "market_proxy" ? "（行情代理）" : ""} · 不构成投资建议
      </p>
    </div>
  );
}
