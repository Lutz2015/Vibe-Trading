import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Loader2,
  Play,
  RefreshCw,
  ShieldAlert,
  Square,
  Wallet,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import {
  api,
  type QbitAutomationStatus,
  type QbitHealthResponse,
  type QbitLedgerSnapshot,
  type QbitOpsStatus,
  type QbitTradeRecord,
} from "@/lib/api";

function fmtMoney(value: number): string {
  return value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

type Tab = "ops" | "backtest" | "monitor" | "strategies";

export function QuantDesk() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>("ops");
  const [loading, setLoading] = useState(false);
  const [health, setHealth] = useState<QbitHealthResponse | null>(null);
  const [automation, setAutomation] = useState<QbitAutomationStatus | null>(null);
  const [ops, setOps] = useState<QbitOpsStatus | null>(null);
  const [snapshot, setSnapshot] = useState<QbitLedgerSnapshot | null>(null);
  const [trades, setTrades] = useState<QbitTradeRecord[]>([]);
  const [resetCash, setResetCash] = useState(1_000_000);
  const [error, setError] = useState<string | null>(null);

  // backtest form
  const [btSymbol, setBtSymbol] = useState("600519.SH");
  const [btStart, setBtStart] = useState("2024-01-01");
  const [btEnd, setBtEnd] = useState("2024-12-31");
  const [btShort, setBtShort] = useState(5);
  const [btLong, setBtLong] = useState(20);
  const [btResult, setBtResult] = useState<Record<string, unknown> | null>(null);
  const [btLoading, setBtLoading] = useState(false);

  // monitor
  const [metrics, setMetrics] = useState<Record<string, number> | null>(null);
  const [alerts, setAlerts] = useState<
    Array<{ metric: string; current_value: number; threshold: number; triggered: boolean }>
  >([]);

  // strategies
  const [strategies, setStrategies] = useState<Array<Record<string, unknown>>>([]);

  const pnl = useMemo(() => {
    if (!snapshot) return 0;
    return snapshot.portfolio_value - snapshot.initial_cash;
  }, [snapshot]);

  const pnlPct = useMemo(() => {
    if (!snapshot || snapshot.initial_cash <= 0) return 0;
    return (pnl / snapshot.initial_cash) * 100;
  }, [pnl, snapshot]);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [healthData, automationData, opsData, snapshotData, tradesData] = await Promise.all([
        api.qbitHealth(),
        api.qbitAutomationStatus(),
        api.qbitOpsStatus(),
        api.qbitLedgerSnapshot(),
        api.qbitLedgerTrades(100),
      ]);
      setHealth(healthData);
      setAutomation(automationData);
      setOps(opsData);
      setSnapshot(snapshotData);
      setTrades(tradesData);
      try {
        const [m, a, s] = await Promise.all([
          api.qbitMonitoringMetrics(),
          api.qbitMonitoringAlerts(),
          api.qbitStrategyRepository(),
        ]);
        setMetrics(m);
        // alerts endpoint returns { has_alerts, alerts: [{metric,current_value,threshold,triggered}], metrics }
        const alertRows = Array.isArray((a as { alerts?: unknown })?.alerts)
          ? ((a as { alerts: Array<Record<string, unknown>> }).alerts)
          : [];
        setAlerts(
          alertRows.map((row) => ({
            metric: String(row.metric ?? ""),
            current_value: Number(row.current_value ?? 0),
            threshold: Number(row.threshold ?? 0),
            triggered: Boolean(row.triggered),
          })),
        );
        const fromAlerts = (a as { metrics?: Record<string, number> }).metrics;
        setMetrics(m && Object.keys(m).length ? m : fromAlerts || m);
        setStrategies(s);
      } catch {
        /* optional panels */
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : t("quant.unavailable", { defaultValue: "Qbit 服务不可用" }));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void loadAll();
    const id = window.setInterval(() => void loadAll(), 60_000);
    return () => window.clearInterval(id);
  }, [loadAll]);

  const handleRunCycle = async () => {
    try {
      const result = await api.qbitRunCycle(true);
      if (result.skipped) {
        setError(`${t("quant.skipped", { defaultValue: "未执行" })}：${result.skip_reason || "skipped"}`);
      } else {
        setError(null);
      }
      await loadAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("quant.cycleFailed", { defaultValue: "触发失败" }));
    }
  };

  const handleKillSwitch = async () => {
    try {
      const next = !(ops?.killSwitch ?? false);
      setOps(await api.qbitKillSwitch(next));
      await loadAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Kill switch 失败");
    }
  };

  const handleTradingToggle = async () => {
    try {
      const next = !(ops?.tradingEnabled ?? true);
      setOps(await api.qbitTradingToggle(next));
      await loadAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : "交易开关失败");
    }
  };

  const runBacktest = async () => {
    setBtLoading(true);
    setError(null);
    try {
      const res = await api.qbitQuickBacktest({
        symbol: btSymbol,
        start: btStart,
        end: btEnd,
        short_window: btShort,
        long_window: btLong,
      });
      setBtResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "回测失败");
    } finally {
      setBtLoading(false);
    }
  };

  const tabs: { id: Tab; label: string }[] = [
    { id: "ops", label: t("quant.tabOps", { defaultValue: "运维" }) },
    { id: "backtest", label: t("quant.tabBacktest", { defaultValue: "快速回测" }) },
    { id: "monitor", label: t("quant.tabMonitor", { defaultValue: "监控" }) },
    { id: "strategies", label: t("quant.tabStrategies", { defaultValue: "策略仓库" }) },
  ];

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Activity className="h-6 w-6 text-primary" />
            <h1 className="text-2xl font-bold">{t("quant.title", { defaultValue: "量化交易台" })}</h1>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            {t("quant.subtitle", {
              defaultValue: "Qbit 引擎 · 模拟盘 · 风控 · 策略编排 · 自动化再平衡",
            })}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void loadAll()}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-muted disabled:opacity-60"
        >
          <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          {t("common.refresh", { defaultValue: "刷新" })}
        </button>
      </div>

      {error ? (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
          {error}
        </div>
      ) : null}

      <div className="grid gap-4 md:grid-cols-4">
        <div className="rounded-xl border bg-card p-4">
          <div className="text-xs text-muted-foreground">{t("quant.nav", { defaultValue: "组合净值" })}</div>
          <div className="mt-1 text-2xl font-bold tabular-nums">
            {snapshot ? fmtMoney(snapshot.portfolio_value) : "—"}
          </div>
        </div>
        <div className="rounded-xl border bg-card p-4">
          <div className="text-xs text-muted-foreground">{t("quant.pnl", { defaultValue: "浮动盈亏" })}</div>
          <div
            className={cn(
              "mt-1 text-2xl font-bold tabular-nums",
              pnl >= 0 ? "text-rose-500" : "text-emerald-500",
            )}
          >
            {snapshot ? `${pnl >= 0 ? "+" : ""}${fmtMoney(pnl)} (${pnlPct.toFixed(2)}%)` : "—"}
          </div>
        </div>
        <div className="rounded-xl border bg-card p-4">
          <div className="text-xs text-muted-foreground">{t("quant.cash", { defaultValue: "现金" })}</div>
          <div className="mt-1 text-2xl font-bold tabular-nums">
            {snapshot ? fmtMoney(snapshot.cash) : "—"}
          </div>
        </div>
        <div className="rounded-xl border bg-card p-4">
          <div className="text-xs text-muted-foreground">{t("quant.scheduler", { defaultValue: "调度器" })}</div>
          <div className="mt-1 text-sm font-medium">
            {automation?.scheduler_running
              ? t("quant.running", { defaultValue: "运行中" })
              : t("quant.stopped", { defaultValue: "已停止" })}
            {health?.auto_trading_enabled ? " · auto" : ""}
          </div>
          <div className="mt-1 text-[11px] text-muted-foreground">
            {automation?.last_run_status ?? "—"} {automation?.last_run_detail ?? ""}
          </div>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => void handleRunCycle()}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"
        >
          <Play className="h-4 w-4" />
          {t("quant.runCycle", { defaultValue: "手动跑一轮" })}
        </button>
        <button
          type="button"
          onClick={() => void api.qbitStartAutomation().then(loadAll)}
          className="inline-flex items-center gap-1.5 rounded-md border px-3 py-2 text-sm hover:bg-muted"
        >
          {t("quant.startScheduler", { defaultValue: "启动调度" })}
        </button>
        <button
          type="button"
          onClick={() => void api.qbitStopAutomation().then(loadAll)}
          className="inline-flex items-center gap-1.5 rounded-md border px-3 py-2 text-sm hover:bg-muted"
        >
          <Square className="h-4 w-4" />
          {t("quant.stopScheduler", { defaultValue: "停止调度" })}
        </button>
        <button
          type="button"
          onClick={() => void handleTradingToggle()}
          className="inline-flex items-center gap-1.5 rounded-md border px-3 py-2 text-sm hover:bg-muted"
        >
          {ops?.tradingEnabled
            ? t("quant.disableTrading", { defaultValue: "禁用交易" })
            : t("quant.enableTrading", { defaultValue: "启用交易" })}
        </button>
        <button
          type="button"
          onClick={() => void handleKillSwitch()}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-md border px-3 py-2 text-sm",
            ops?.killSwitch
              ? "border-rose-500/50 bg-rose-500/10 text-rose-600"
              : "hover:bg-muted",
          )}
        >
          <ShieldAlert className="h-4 w-4" />
          {ops?.killSwitch ? "Kill Switch ON" : "Kill Switch"}
        </button>
        <div className="inline-flex items-center gap-2 rounded-md border px-2 py-1">
          <Wallet className="h-4 w-4 text-muted-foreground" />
          <input
            type="number"
            value={resetCash}
            onChange={(e) => setResetCash(Number(e.target.value))}
            className="w-28 bg-transparent text-sm outline-none"
          />
          <button
            type="button"
            onClick={() => void api.qbitResetLedger(resetCash).then(loadAll)}
            className="text-xs text-primary hover:underline"
          >
            {t("quant.resetLedger", { defaultValue: "重置账本" })}
          </button>
        </div>
      </div>

      {ops && !ops.tradingEnabled ? (
        <div className="flex items-center gap-2 rounded-md border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-xs text-rose-600">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          {t("quant.tradingDisabled", { defaultValue: "交易已禁用（风控或 Kill Switch）" })}
        </div>
      ) : null}

      {/* Tabs */}
      <div className="flex flex-wrap gap-1 border-b">
        {tabs.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => setTab(item.id)}
            className={cn(
              "px-3 py-2 text-sm",
              tab === item.id
                ? "border-b-2 border-primary font-medium text-primary"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === "ops" ? (
        <section className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-xl border bg-card p-4">
            <h2 className="mb-3 text-sm font-semibold">{t("quant.positions", { defaultValue: "持仓" })}</h2>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="pb-2">{t("quant.symbol", { defaultValue: "代码" })}</th>
                  <th className="pb-2 text-right">{t("quant.qty", { defaultValue: "数量" })}</th>
                </tr>
              </thead>
              <tbody>
                {(snapshot?.positions ?? []).map((p) => (
                  <tr key={p.symbol} className="border-b border-border/50">
                    <td className="py-2 font-mono text-xs">{p.symbol}</td>
                    <td className="py-2 text-right tabular-nums">{p.quantity}</td>
                  </tr>
                ))}
                {!snapshot?.positions?.length ? (
                  <tr>
                    <td colSpan={2} className="py-4 text-center text-muted-foreground">
                      {t("quant.noPositions", { defaultValue: "暂无持仓" })}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
          <div className="rounded-xl border bg-card p-4">
            <h2 className="mb-3 text-sm font-semibold">{t("quant.trades", { defaultValue: "最近成交" })}</h2>
            <div className="max-h-72 overflow-y-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs text-muted-foreground">
                    <th className="pb-2">{t("quant.time", { defaultValue: "时间" })}</th>
                    <th className="pb-2">{t("quant.symbol", { defaultValue: "标的" })}</th>
                    <th className="pb-2">{t("quant.side", { defaultValue: "方向" })}</th>
                    <th className="pb-2 text-right">{t("quant.qty", { defaultValue: "数量" })}</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((tr, i) => (
                    <tr key={`${tr.ts}-${tr.symbol}-${i}`} className="border-b border-border/50">
                      <td className="py-1.5 text-[11px] text-muted-foreground">
                        {new Date(tr.ts).toLocaleString("zh-CN", { hour12: false })}
                      </td>
                      <td className="py-1.5 font-mono text-xs">{tr.symbol}</td>
                      <td className={cn("py-1.5", tr.side === "BUY" ? "text-rose-500" : "text-emerald-500")}>
                        {tr.side}
                      </td>
                      <td className="py-1.5 text-right tabular-nums">{tr.quantity}</td>
                    </tr>
                  ))}
                  {!trades.length ? (
                    <tr>
                      <td colSpan={4} className="py-4 text-center text-muted-foreground">
                        {t("quant.noTrades", { defaultValue: "暂无成交" })}
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      ) : null}

      {tab === "backtest" ? (
        <section className="rounded-xl border bg-card p-4">
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <BarChart3 className="h-4 w-4 text-primary" />
            {t("quant.quickBacktest", { defaultValue: "SMA 快速回测（自动拉行情）" })}
          </h2>
          <div className="grid gap-3 md:grid-cols-5">
            <label className="text-xs">
              {t("quant.symbol", { defaultValue: "代码" })}
              <input
                value={btSymbol}
                onChange={(e) => setBtSymbol(e.target.value)}
                className="mt-1 w-full rounded-md border bg-background px-2 py-1.5 text-sm"
              />
            </label>
            <label className="text-xs">
              Start
              <input
                value={btStart}
                onChange={(e) => setBtStart(e.target.value)}
                className="mt-1 w-full rounded-md border bg-background px-2 py-1.5 text-sm"
              />
            </label>
            <label className="text-xs">
              End
              <input
                value={btEnd}
                onChange={(e) => setBtEnd(e.target.value)}
                className="mt-1 w-full rounded-md border bg-background px-2 py-1.5 text-sm"
              />
            </label>
            <label className="text-xs">
              Fast
              <input
                type="number"
                value={btShort}
                onChange={(e) => setBtShort(Number(e.target.value))}
                className="mt-1 w-full rounded-md border bg-background px-2 py-1.5 text-sm"
              />
            </label>
            <label className="text-xs">
              Slow
              <input
                type="number"
                value={btLong}
                onChange={(e) => setBtLong(Number(e.target.value))}
                className="mt-1 w-full rounded-md border bg-background px-2 py-1.5 text-sm"
              />
            </label>
          </div>
          <button
            type="button"
            disabled={btLoading}
            onClick={() => void runBacktest()}
            className="mt-3 inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
          >
            {btLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            {t("quant.runBacktest", { defaultValue: "开始回测" })}
          </button>
          {btResult ? (
            <pre className="mt-4 max-h-80 overflow-auto rounded-md bg-muted/40 p-3 text-[11px] leading-relaxed">
              {JSON.stringify(btResult, null, 2)}
            </pre>
          ) : null}
        </section>
      ) : null}

      {tab === "monitor" ? (
        <section className="grid gap-4 md:grid-cols-2">
          <div className="rounded-xl border bg-card p-4">
            <h2 className="mb-3 text-sm font-semibold">{t("quant.metrics", { defaultValue: "运行指标" })}</h2>
            <div className="grid grid-cols-2 gap-2 text-sm">
              {Object.entries(metrics || {}).map(([k, v]) => (
                <div key={k} className="rounded-md bg-muted/30 px-2 py-1.5">
                  <div className="text-[11px] text-muted-foreground">{k}</div>
                  <div className="font-medium tabular-nums">{v}</div>
                </div>
              ))}
              {!metrics ? <p className="text-muted-foreground">—</p> : null}
            </div>
          </div>
          <div className="rounded-xl border bg-card p-4">
            <h2 className="mb-3 text-sm font-semibold">{t("quant.alerts", { defaultValue: "告警" })}</h2>
            {alerts.length ? (
              <ul className="space-y-1 text-sm">
                {alerts.map((a) => (
                  <li
                    key={a.metric}
                    className={cn(
                      "rounded px-2 py-1",
                      a.triggered
                        ? "bg-rose-500/10 text-rose-600"
                        : "bg-muted/30 text-muted-foreground",
                    )}
                  >
                    <span className="font-medium">{a.metric}</span>
                    {" · "}
                    {a.current_value} / {a.threshold}
                    {a.triggered ? " · 触发" : ""}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">
                {t("quant.noAlerts", { defaultValue: "暂无告警" })}
              </p>
            )}
            <button
              type="button"
              onClick={() => void api.qbitReconcile().then(loadAll)}
              className="mt-3 rounded-md border px-3 py-1.5 text-xs hover:bg-muted"
            >
              {t("quant.reconcile", { defaultValue: "执行对账" })}
            </button>
          </div>
        </section>
      ) : null}

      {tab === "strategies" ? (
        <section className="rounded-xl border bg-card p-4">
          <h2 className="mb-3 text-sm font-semibold">{t("quant.repo", { defaultValue: "策略仓库" })}</h2>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th className="pb-2">ID</th>
                <th className="pb-2">Version</th>
                <th className="pb-2">Class</th>
              </tr>
            </thead>
            <tbody>
              {strategies.map((s, i) => (
                <tr key={String(s.strategy_id || i)} className="border-b border-border/50">
                  <td className="py-2 font-mono text-xs">{String(s.strategy_id)}</td>
                  <td className="py-2 text-xs">{String(s.version)}</td>
                  <td className="py-2 text-xs">{String(s.asset_class)}</td>
                </tr>
              ))}
              {!strategies.length ? (
                <tr>
                  <td colSpan={3} className="py-4 text-center text-muted-foreground">
                    —
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </section>
      ) : null}

      <p className="text-center text-[11px] text-muted-foreground">
        API /qbit · {t("quant.footnote", { defaultValue: "数据 ~/.person-trading/qbit/ · 默认 paper · 非投资建议" })}
      </p>
    </div>
  );
}
