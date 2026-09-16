import { useEffect, useMemo, useState } from "react";
import { Activity, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import { AiInsightPanel } from "@/components/common/AiInsightPanel";

type SentimentItem = {
  id: string;
  title: string;
  source: "Polymarket" | "Kalshi";
  category: string;
  probability: number; // 0-100
  delta24h: number; // percentage points
  horizon: string;
  note?: string;
};

const SEED: SentimentItem[] = [
  {
    id: "fed_cut",
    title: "美联储年内至少再降息一次",
    source: "Polymarket",
    category: "货币政策",
    probability: 68,
    delta24h: 3.2,
    horizon: "2026 年内",
    note: "通胀路径与就业数据共同定价",
  },
  {
    id: "us_recession",
    title: "美国未来 12 个月陷入衰退",
    source: "Kalshi",
    category: "增长",
    probability: 22,
    delta24h: -1.4,
    horizon: "未来 12 个月",
  },
  {
    id: "cn_stimulus",
    title: "中国出台超预期稳增长一揽子",
    source: "Polymarket",
    category: "中国政策",
    probability: 41,
    delta24h: 2.1,
    horizon: "未来 2 个季度",
  },
  {
    id: "ai_capex",
    title: "全球 AI 资本开支同比增速 >30%",
    source: "Kalshi",
    category: "科技",
    probability: 57,
    delta24h: 4.8,
    horizon: "2026 全年",
    note: "云厂商 CapEx 指引隐含概率",
  },
  {
    id: "oil_90",
    title: "布伦特原油年内触及 90 美元",
    source: "Polymarket",
    category: "大宗",
    probability: 33,
    delta24h: -0.6,
    horizon: "2026 年内",
  },
  {
    id: "btc_ath",
    title: "比特币再创历史新高",
    source: "Kalshi",
    category: "加密",
    probability: 61,
    delta24h: 5.5,
    horizon: "未来 6 个月",
  },
];

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
  const [items, setItems] = useState(SEED);
  const [updatedAt, setUpdatedAt] = useState(() => new Date());
  const [refreshing, setRefreshing] = useState(false);

  const composite = useMemo(() => {
    if (items.length === 0) return 50;
    // Weight risk-on proxies higher: AI capex, BTC ATH, stimulus; recession inverted.
    const weights: Record<string, number> = {
      ai_capex: 1.4,
      btc_ath: 1.2,
      cn_stimulus: 1.2,
      us_recession: -1.3,
      fed_cut: 1.0,
      oil_90: 0.6,
    };
    let sum = 0;
    let weightSum = 0;
    for (const item of items) {
      const w = weights[item.id] ?? 1;
      const value = w < 0 ? 100 - item.probability : item.probability;
      sum += value * Math.abs(w);
      weightSum += Math.abs(w);
    }
    return Math.round((sum / Math.max(weightSum, 1)) * 10) / 10;
  }, [items]);

  const refresh = () => {
    setRefreshing(true);
    setItems((prev) =>
      prev.map((item) => {
        const delta = (Math.random() - 0.5) * 2.4;
        const next = Math.max(2, Math.min(98, item.probability + delta));
        return {
          ...item,
          probability: Math.round(next * 10) / 10,
          delta24h: Math.round((item.delta24h + delta * 0.4) * 10) / 10,
        };
      }),
    );
    setUpdatedAt(new Date());
    window.setTimeout(() => setRefreshing(false), 350);
  };

  useEffect(() => {
    const id = window.setInterval(refresh, 60_000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6 p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Activity className="h-6 w-6 text-primary" />
            <h1 className="text-2xl font-bold">情绪温度计</h1>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            把全球宏观的预期概率，融入投研体系 · 参考 Polymarket / Kalshi 公开预期
          </p>
        </div>
        <button
          type="button"
          onClick={refresh}
          disabled={refreshing}
          className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm text-muted-foreground hover:bg-muted disabled:opacity-60"
        >
          <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />
          刷新
        </button>
      </div>

      <section className="rounded-2xl border bg-card p-6 shadow-sm">
        <div className="grid items-center gap-6 md:grid-cols-[280px_1fr]">
          <Gauge value={composite} />
          <div className="space-y-3">
            <h2 className="text-sm font-semibold">解读</h2>
            <p className="text-sm leading-relaxed text-muted-foreground">
              综合温度由多项宏观预期概率加权得到：降息、衰退、中国政策、AI
              资本开支、原油与加密等。数值越高代表市场对「风险资产友好」情景的定价越充分；
              过热时需警惕拥挤交易，冰冷时则可能是逆向观察窗口。本页数据为界面演示，可对接公开
              预测市场 API 后替换。
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
        {items.map((item) => {
          const h = heat(item.probability);
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
        autoRun={items.length > 0}
        runKey={updatedAt.toISOString()}
        payload={{
          composite,
          items: items.map((i) => ({
            title: i.title,
            probability: i.probability,
            delta24h: i.delta24h,
            source: i.source,
            category: i.category,
          })),
        }}
      />

      <p className="text-center text-[11px] text-muted-foreground">
        更新：{updatedAt.toLocaleString("zh-CN", { hour12: false })} ·
        免责：演示数据不构成任何投资建议，市场有风险，决策需独立判断
      </p>
    </div>
  );
}
