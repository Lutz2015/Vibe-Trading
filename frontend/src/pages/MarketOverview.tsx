import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, TrendingUp, TrendingDown, Flame } from "lucide-react";
import { AiInsightPanel } from "@/components/common/AiInsightPanel";
import { cn } from "@/lib/utils";
import {
  api,
  type HotBoard as HotBoardItem,
  type MarketOverviewResponse,
  type MarketQuoteItem,
} from "@/lib/api";

function pct(value?: number | null): string {
  if (value == null || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function ChangeBadge({ value }: { value?: number | null }) {
  if (value == null || Number.isNaN(value)) {
    return <span className="text-xs text-muted-foreground">—</span>;
  }
  const up = value >= 0;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 font-medium tabular-nums",
        up ? "text-rose-500" : "text-emerald-500",
      )}
    >
      {up ? <TrendingUp className="h-3.5 w-3.5" /> : <TrendingDown className="h-3.5 w-3.5" />}
      {pct(value)}
    </span>
  );
}

function QuoteTable({ rows, dense }: { rows: MarketQuoteItem[]; dense?: boolean }) {
  if (!rows.length) {
    return (
      <p className="px-4 py-3 text-xs text-muted-foreground">暂无数据</p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs text-muted-foreground">
            <th className="px-3 py-1.5 text-start font-medium">名称</th>
            <th className="px-2 py-1.5 text-end font-medium">现价</th>
            <th className="px-3 py-1.5 text-end font-medium">涨跌</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.symbol} className="border-t hover:bg-muted/30">
              <td className={cn("px-3", dense ? "py-1.5" : "py-2")}>
                <div className="font-medium">{row.name}</div>
                {row.sub ? (
                  <div className="text-[10px] text-muted-foreground">{row.sub}</div>
                ) : null}
              </td>
              <td className="px-2 text-end tabular-nums">
                {row.price == null ? "—" : row.price.toFixed(2)}
              </td>
              <td className="px-3 text-end">
                <ChangeBadge value={row.change_pct} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BoardCard({ board }: { board: HotBoardItem }) {
  return (
    <article className="rounded-xl border bg-card shadow-sm">
      <header className="flex items-start justify-between gap-2 border-b px-4 py-3">
        <div>
          <div className="flex items-center gap-1.5">
            <Flame className="h-3.5 w-3.5 text-orange-500" />
            <h3 className="text-sm font-semibold">{board.board_name}</h3>
          </div>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            {board.kind === "concept" ? "概念板块" : "行业板块"}
            {board.board_code ? ` · ${board.board_code}` : ""}
          </p>
        </div>
        <ChangeBadge value={board.change_pct} />
      </header>
      <QuoteTable rows={board.leaders || []} dense />
    </article>
  );
}

export function MarketOverview() {
  const [data, setData] = useState<MarketOverviewResponse | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const res = await api.fetchMarketOverview();
      setData(res);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "行情获取失败");
    } finally {
      setUpdatedAt(new Date());
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const id = window.setInterval(() => void load(), 60_000);
    return () => window.clearInterval(id);
  }, [load]);

  const stamp = useMemo(
    () => (updatedAt ? updatedAt.toLocaleString("zh-CN", { hour12: false }) : "—"),
    [updatedAt],
  );

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5 p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <TrendingUp className="h-6 w-6 text-primary" />
            <h1 className="text-2xl font-bold">市场总览</h1>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            实时大盘 · 热点行业/概念板块 · 涨幅榜个股 · 东方财富公开源（60s 刷新）
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

      <section>
        <h2 className="mb-2 text-sm font-medium text-muted-foreground">大盘指数</h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7">
          {(data?.indices || []).map((idx) => (
            <article key={idx.symbol} className="rounded-xl border bg-card px-3 py-3 shadow-sm">
              <div className="text-xs text-muted-foreground">{idx.name}</div>
              <div className="mt-1 text-xl font-semibold tabular-nums">
                {idx.price == null ? "—" : idx.price.toFixed(2)}
              </div>
              <div className="mt-1">
                <ChangeBadge value={idx.change_pct} />
              </div>
            </article>
          ))}
          {!data && !error
            ? Array.from({ length: 7 }).map((_, i) => (
                <div key={i} className="h-24 animate-pulse rounded-xl bg-muted/40" />
              ))
            : null}
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm font-medium text-muted-foreground">热点板块（涨幅居前）</h2>
        <div className="grid gap-4 md:grid-cols-2">
          {(data?.hot_boards || []).map((board) => (
            <BoardCard key={board.board_code || board.board_name} board={board} />
          ))}
          {data && (data.hot_boards || []).length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无板块数据</p>
          ) : null}
          {!data && !error
            ? [1, 2, 3, 4].map((i) => (
                <div key={i} className="h-40 animate-pulse rounded-xl bg-muted/40" />
              ))
            : null}
        </div>
      </section>

      <section className="rounded-xl border bg-card shadow-sm">
        <header className="border-b px-4 py-3">
          <h2 className="text-sm font-semibold">涨幅榜热门个股</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            A 股涨幅居前（已过滤 ST）
          </p>
        </header>
        <QuoteTable rows={data?.hot_stocks || []} />
      </section>

      <AiInsightPanel
        kind="market"
        title="AI 市场解读"
        autoRun={Boolean(data && (data.hot_boards?.length || data.hot_stocks?.length))}
        runKey={stamp}
        payload={{
          indices: data?.indices || [],
          hot_boards: data?.hot_boards || [],
          hot_stocks: data?.hot_stocks || [],
        }}
      />

      <p className="text-center text-[11px] text-muted-foreground">
        更新：{stamp} · 数据源：东方财富公开接口 · AI 解读依赖已配置的 LLM · 非投资建议
      </p>
    </div>
  );
}
