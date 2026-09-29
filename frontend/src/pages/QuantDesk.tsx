import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  ChevronDown,
  ChevronRight,
  Gauge,
  Loader2,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  Square,
  Wallet,
  Zap,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { ModuleCopilot } from "@/components/common/ModuleCopilot";
import { TradingCycleAgentLog } from "@/components/trading/TradingCycleAgentLog";
import { formatHaltDetail, isHaltActive } from "@/lib/haltStatus";
import { formatLiveGateReasons, isLiveGateBlocked } from "@/lib/liveGate";
import {
  api,
  type QbitAutomationStatus,
  type QbitLedgerSnapshot,
  type QbitOpsStatus,
  type QbitRunCycleResponse,
  type QbitTradeRecord,
} from "@/lib/api";

function fmtMoney(value: number): string {
  return value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtNum(value: number, digits = 2): string {
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** Chinese name is primary; bare code only when no name is known. */
function displayName(name: string | null | undefined, symbol: string): string {
  const trimmed = (name ?? "").trim();
  return trimmed || symbol;
}

function subCode(name: string | null | undefined, symbol: string): string | null {
  return (name ?? "").trim() ? symbol : null;
}

type Pane = "cockpit" | "strategies" | "backtest" | "monitor";

export function QuantDesk() {
  const { t } = useTranslation();
  const [pane, setPane] = useState<Pane>("cockpit");
  const [loading, setLoading] = useState(false);
  const [runningCycle, setRunningCycle] = useState(false);
  const [automation, setAutomation] = useState<QbitAutomationStatus | null>(null);
  const [ops, setOps] = useState<QbitOpsStatus | null>(null);
  const [snapshot, setSnapshot] = useState<QbitLedgerSnapshot | null>(null);
  const [trades, setTrades] = useState<QbitTradeRecord[]>([]);
  const [resetCash, setResetCash] = useState(1_000_000);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [cycleResult, setCycleResult] = useState<QbitRunCycleResponse | null>(null);
  const [showTrades, setShowTrades] = useState(false);

  const [btSymbol, setBtSymbol] = useState("600519.SH");
  const [btStart, setBtStart] = useState("2024-01-01");
  const [btEnd, setBtEnd] = useState("2024-12-31");
  const [btShort, setBtShort] = useState(5);
  const [btLong, setBtLong] = useState(20);
  const [btResult, setBtResult] = useState<Record<string, unknown> | null>(null);
  const [btLoading, setBtLoading] = useState(false);

  const [metrics, setMetrics] = useState<Record<string, number> | null>(null);
  const [alerts, setAlerts] = useState<
    Array<{ metric: string; current_value: number; threshold: number; triggered: boolean }>
  >([]);
  const [strategies, setStrategies] = useState<Array<Record<string, unknown>>>([]);
  const [selectingStrategy, setSelectingStrategy] = useState<string | null>(null);
  const [topNDraft, setTopNDraft] = useState<number>(4);
  const [modeDraft, setModeDraft] = useState<string>("momentum");
  const [genIntent, setGenIntent] = useState("");
  const [genText, setGenText] = useState("");
  const [genLoading, setGenLoading] = useState(false);

  const pnl = useMemo(() => {
    if (!snapshot) return 0;
    return snapshot.portfolio_value - snapshot.initial_cash;
  }, [snapshot]);

  const pnlPct = useMemo(() => {
    if (!snapshot || snapshot.initial_cash <= 0) return 0;
    return (pnl / snapshot.initial_cash) * 100;
  }, [pnl, snapshot]);

  const liveGateBlocked = useMemo(() => isLiveGateBlocked(automation), [automation]);
  const liveGateReasonText = useMemo(
    () => formatLiveGateReasons(automation?.live_gate?.reasons, t),
    [automation?.live_gate?.reasons, t],
  );

  const haltActive = useMemo(() => isHaltActive(ops), [ops]);
  const haltDetailText = useMemo(
    () => formatHaltDetail(ops?.halt, t),
    [ops?.halt, t],
  );

  const positionValue = useMemo(() => {
    if (!snapshot) return 0;
    return snapshot.position_value || 0;
  }, [snapshot]);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [, automationData, opsData, snapshotData, tradesData] = await Promise.all([
        api.qbitHealth(),
        api.qbitAutomationStatus(),
        api.qbitOpsStatus(),
        api.qbitLedgerSnapshot(),
        api.qbitLedgerTrades(50),
      ]);
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
    setRunningCycle(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.qbitRunCycle(true);
      setCycleResult(result);
      if (result.skipped) {
        setNotice(`${t("quant.skipped", { defaultValue: "未执行" })}：${result.skip_reason || "skipped"}`);
      } else {
        setNotice(result.message || t("quant.cycleDone", { defaultValue: "本轮已完成" }));
      }
      await loadAll();
    } catch (e) {
      setCycleResult(null);
      setError(e instanceof Error ? e.message : t("quant.cycleFailed", { defaultValue: "触发失败" }));
    } finally {
      setRunningCycle(false);
    }
  };

  const handleKillSwitch = async () => {
    try {
      const next = !haltActive;
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

  const handleSelectStrategy = async (strategyId: string) => {
    setSelectingStrategy(strategyId);
    setError(null);
    try {
      const status = await api.qbitSelectStrategies([strategyId]);
      setAutomation(status);
      setNotice(
        t("quant.strategySelected", {
          defaultValue: `已切换策略：${strategyId}`,
          id: strategyId,
        }),
      );
      await loadAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : "切换策略失败");
    } finally {
      setSelectingStrategy(null);
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

  const activeStrategies = useMemo(
    () => (automation?.strategies ?? []).filter((s) => s.enabled),
    [automation],
  );

  const selectedStrategy = useMemo(
    () => (automation?.strategies ?? []).find((s) => s.enabled) ?? automation?.strategies?.[0],
    [automation],
  );

  useEffect(() => {
    if (!selectedStrategy) return;
    setTopNDraft(Number(selectedStrategy.top_n ?? 4));
    setModeDraft(String(selectedStrategy.mode ?? "momentum"));
  }, [selectedStrategy]);

  const handleSaveParams = async () => {
    if (!selectedStrategy) return;
    const id = String(selectedStrategy.strategy_id);
    try {
      const status = await api.qbitPatchStrategy(id, {
        top_n: topNDraft,
        mode: modeDraft,
      });
      setAutomation(status);
      setNotice(
        t("quant.paramsSaved", { defaultValue: `已更新 ${id} 参数` }),
      );
      await loadAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存参数失败");
    }
  };

  const handleGenerateStrategy = async () => {
    if (!genIntent.trim()) return;
    setGenLoading(true);
    setGenText("");
    try {
      const res = await api.analyzeInsight({
        kind: "strategy_gen",
        payload: { intent: genIntent },
        locale: navigator.language || "zh-CN",
      });
      setGenText(res.text || res.error || "生成失败");
    } catch (e) {
      setGenText(e instanceof Error ? e.message : "生成失败");
    } finally {
      setGenLoading(false);
    }
  };

  const panes: { id: Pane; label: string; icon: typeof Gauge }[] = [
    { id: "cockpit", label: t("quant.paneCockpit", { defaultValue: "策略驾驶舱" }), icon: Gauge },
    { id: "strategies", label: t("quant.paneStrategies", { defaultValue: "策略仓库" }), icon: Zap },
    { id: "backtest", label: t("quant.paneBacktest", { defaultValue: "回测验证" }), icon: BarChart3 },
    { id: "monitor", label: t("quant.paneMonitor", { defaultValue: "运行监控" }), icon: Activity },
  ];

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5 p-6">
      {/* Header */}
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Activity className="h-6 w-6 text-primary" />
            <h1 className="text-2xl font-bold tracking-tight">
              {t("quant.title", { defaultValue: "量化交易台" })}
            </h1>
            <span
              className={cn(
                "rounded-full px-2 py-0.5 text-[11px] font-medium",
                haltActive
                  ? "bg-rose-500/15 text-rose-500"
                  : automation?.scheduler_running
                    ? "bg-emerald-500/15 text-emerald-500"
                    : "bg-muted text-muted-foreground",
              )}
            >
              {haltActive
                ? t("tradingCenter.halted", { defaultValue: "已熔断" })
                : automation?.scheduler_running
                  ? t("quant.autoOn", { defaultValue: "自动运行中" })
                  : t("quant.manualOnly", { defaultValue: "仅手动" })}
            </span>
          </div>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            {t("quant.purpose", {
              defaultValue:
                "策略自动选标的、自动再平衡；你可以随时手动跑一轮、暂停调度或急停。模拟盘优先，实盘需额外授权。",
            })}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void loadAll()}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-muted disabled:opacity-60"
          >
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
            {t("common.refresh", { defaultValue: "刷新" })}
          </button>
          <button
            type="button"
            onClick={() => void handleRunCycle()}
            disabled={runningCycle || liveGateBlocked}
            className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:opacity-90 disabled:opacity-60"
          >
            {runningCycle ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            {t("quant.runCycle", { defaultValue: "立即跑一轮" })}
          </button>
        </div>
      </header>

      {liveGateBlocked ? (
        <div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <div className="font-medium">{t("liveGate.blockedTitle", { defaultValue: "实盘门禁未通过" })}</div>
            <div className="mt-0.5 text-xs">{liveGateReasonText}</div>
            <div className="mt-1 text-xs opacity-80">
              {t("liveGate.blockedHint", {
                defaultValue: "请先提交 mandate、配置 live_broker，并设置 EXECUTION_LIVE_ENABLED=true。",
              })}
            </div>
          </div>
        </div>
      ) : null}

      {error ? (
        <div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}
      {notice ? (
        <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300">
          {notice}
        </div>
      ) : null}

      {/* Status strip */}
      <section className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
        <div className="rounded-xl border bg-card px-4 py-3">
          <div className="text-[11px] text-muted-foreground">
            {t("quant.nav", { defaultValue: "组合净值" })}
          </div>
          <div className="mt-0.5 text-xl font-bold tabular-nums">
            {snapshot ? fmtMoney(snapshot.portfolio_value) : "—"}
          </div>
        </div>
        <div className="rounded-xl border bg-card px-4 py-3">
          <div className="text-[11px] text-muted-foreground">
            {t("quant.pnl", { defaultValue: "累计盈亏" })}
          </div>
          <div
            className={cn(
              "mt-0.5 text-xl font-bold tabular-nums",
              pnl >= 0 ? "text-rose-500" : "text-emerald-500",
            )}
          >
            {snapshot ? `${pnl >= 0 ? "+" : ""}${fmtMoney(pnl)}` : "—"}
          </div>
          <div className="text-[11px] text-muted-foreground tabular-nums">
            {snapshot ? `${pnlPct.toFixed(2)}%` : ""}
          </div>
        </div>
        <div className="rounded-xl border bg-card px-4 py-3">
          <div className="text-[11px] text-muted-foreground">
            {t("quant.cash", { defaultValue: "现金" })}
          </div>
          <div className="mt-0.5 text-xl font-bold tabular-nums">
            {snapshot ? fmtMoney(snapshot.cash) : "—"}
          </div>
          <div className="text-[11px] text-muted-foreground tabular-nums">
            {snapshot ? `持仓 ${fmtMoney(positionValue)}` : ""}
          </div>
        </div>
        <div className="rounded-xl border bg-card px-4 py-3">
          <div className="text-[11px] text-muted-foreground">
            {t("quant.accountType", { defaultValue: "账户类型" })}
          </div>
          <div className="mt-0.5 text-sm font-semibold">
            {automation?.execution_mode === "live"
              ? t("quant.live", { defaultValue: "A股实盘" })
              : t("quant.paper", { defaultValue: "A股实盘" })}
          </div>
          <div className="mt-1 text-[11px] text-muted-foreground">
            <span className="text-muted-foreground/80">
              {t("quant.tradingSession", { defaultValue: "交易时段" })}
            </span>{" "}
            {automation?.trading_hours?.start ?? "09:30"}–{automation?.trading_hours?.end ?? "15:00"}
          </div>
        </div>
        <div className="rounded-xl border bg-card px-4 py-3">
          <div className="text-[11px] text-muted-foreground">
            {t("quant.scheduler", { defaultValue: "调度器" })}
          </div>
          <div className="mt-0.5 text-sm font-semibold">
            {automation?.scheduler_running
              ? t("quant.running", { defaultValue: "运行中" })
              : t("quant.stopped", { defaultValue: "已停止" })}
          </div>
          <div className="truncate text-[11px] text-muted-foreground">
            {automation?.last_run_status ?? "—"}
          </div>
        </div>
        <div
          className={cn(
            "rounded-xl border bg-card px-4 py-3",
            haltActive && "border-rose-500/30 bg-rose-500/5",
          )}
        >
          <div className="text-[11px] text-muted-foreground">
            {t("tradingCenter.haltStatus", { defaultValue: "熔断 / Halt" })}
          </div>
          <div
            className={cn(
              "mt-0.5 text-sm font-semibold",
              haltActive ? "text-rose-600 dark:text-rose-400" : "",
            )}
          >
            {haltActive
              ? t("tradingCenter.halted", { defaultValue: "已熔断" })
              : t("tradingCenter.normal", { defaultValue: "正常" })}
          </div>
          <div className="text-[11px] text-muted-foreground">
            {haltDetailText ??
              (ops?.tradingEnabled
                ? t("quant.tradingOn", { defaultValue: "交易开启" })
                : t("quant.tradingOff", { defaultValue: "交易关闭" }))}
          </div>
        </div>
      </section>

      {/* Nav panes */}
      <nav className="flex flex-wrap gap-1 border-b">
        {panes.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => setPane(item.id)}
              className={cn(
                "inline-flex items-center gap-1.5 px-3 py-2 text-sm",
                pane === item.id
                  ? "border-b-2 border-primary font-medium text-primary"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              {item.label}
            </button>
          );
        })}
      </nav>

      {pane === "cockpit" ? (
        <div className="flex flex-col gap-4">
          <div className="grid gap-4 lg:grid-cols-5">
            {/* Strategy cockpit */}
            <section className="rounded-xl border bg-card p-4 lg:col-span-3">
              <div className="mb-3 flex items-center justify-between gap-2">
                <h2 className="text-sm font-semibold">
                  {t("quant.activeStrategies", { defaultValue: "当前策略" })}
                </h2>
                <span className="text-[11px] text-muted-foreground">
                  {t("quant.strategyHint", {
                    defaultValue: "按日内涨幅选股 · 等权 · 到点再平衡",
                  })}
                </span>
              </div>

              {activeStrategies.length ? (
                <div className="flex flex-col gap-3">
                  {/* Quick switch */}
                  <div className="flex flex-wrap gap-1.5">
                    {(automation?.strategies ?? []).map((s) => (
                      <button
                        key={String(s.strategy_id)}
                        type="button"
                        disabled={selectingStrategy !== null}
                        onClick={() => void handleSelectStrategy(String(s.strategy_id))}
                        className={cn(
                          "rounded-full border px-2.5 py-1 text-[11px] transition",
                          s.enabled
                            ? "border-primary bg-primary text-primary-foreground"
                            : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground",
                        )}
                      >
                        {String(s.strategy_id)}
                      </button>
                    ))}
                  </div>
                  {activeStrategies.map((s) => (
                    <article
                      key={String(s.strategy_id)}
                      className="rounded-lg border bg-background/40 px-3 py-3"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-semibold">{String(s.strategy_id)}</span>
                        <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] text-primary">
                          {t("quant.strategyRunning", { defaultValue: "启用中" })}
                        </span>
                        <span className="text-[11px] text-muted-foreground">
                          Top {Number(s.top_n ?? 5)} · 股票池 {Number(s.universe_size ?? 0)} 只
                        </span>
                      </div>
                      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                        {String(s.rule || t("quant.defaultRule", {
                          defaultValue:
                            "在股票池中按「现价/昨收-1」排序，取前 N 只等权买入；再平衡窗口内把组合拉回目标权重。",
                        }))}
                      </p>
                      {cycleResult?.strategies?.find((x) => x.strategy_id === s.strategy_id) ? (
                        <p className="mt-2 text-xs text-foreground">
                          {t("quant.lastPick", { defaultValue: "本轮选中" })}：
                          {cycleResult.strategies
                            .find((x) => x.strategy_id === s.strategy_id)
                            ?.picked.map((code) => {
                              const hit = snapshot?.positions?.find((p) => p.symbol === code);
                              return hit?.name || code;
                            })
                            .join("、") || "—"}
                        </p>
                      ) : null}
                    </article>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">
                  {t("quant.noStrategy", {
                    defaultValue: "配置里没有启用中的策略。到「策略仓库」检查，或编辑 auto-trading.yaml。",
                  })}
                </p>
              )}

              {/* Last cycle */}
              {cycleResult ? (
                <div
                  className={cn(
                    "mt-4 rounded-lg border px-3 py-2 text-xs",
                    cycleResult.skipped
                      ? "border-amber-500/30 bg-amber-500/5 text-amber-700 dark:text-amber-300"
                      : "border-emerald-500/30 bg-emerald-500/5 text-emerald-700 dark:text-emerald-300",
                  )}
                >
                  <div className="font-medium">
                    {cycleResult.skipped
                      ? `${t("quant.skipped", { defaultValue: "未执行" })}：${cycleResult.skip_reason}`
                      : cycleResult.message}
                  </div>
                  {cycleResult.orders?.length ? (
                    <ul className="mt-1.5 space-y-0.5">
                      {cycleResult.orders.map((o, i) => (
                        <li key={`${o.symbol}-${i}`}>
                          {displayName(o.name, o.symbol)}
                          {subCode(o.name, o.symbol) ? (
                            <span className="ml-1 font-mono text-[10px] opacity-70">
                              {subCode(o.name, o.symbol)}
                            </span>
                          ) : null}
                          {` · ${o.side === "BUY" ? "买入" : "卖出"} ${o.quantity} @ ${fmtNum(o.price)}`}
                          {` · ${o.status}`}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ) : (
                <p className="mt-4 text-xs text-muted-foreground">
                  {t("quant.clickRunHint", {
                    defaultValue: "点右上角「立即跑一轮」会立刻按当前策略下单，并在这里列出成交。",
                  })}
                </p>
              )}
            </section>

            {/* Manual controls */}
            <section className="rounded-xl border bg-card p-4 lg:col-span-2">
              <h2 className="mb-3 text-sm font-semibold">
                {t("quant.manualOps", { defaultValue: "手动操作" })}
              </h2>
              <div className="flex flex-col gap-2">
                <button
                  type="button"
                  onClick={() => void handleRunCycle()}
                  disabled={runningCycle || liveGateBlocked}
                  className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-3 py-2.5 text-sm font-medium text-primary-foreground disabled:opacity-60"
                >
                  {runningCycle ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Play className="h-4 w-4" />
                  )}
                  {t("quant.runCycle", { defaultValue: "立即跑一轮" })}
                </button>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    disabled={liveGateBlocked}
                    onClick={() => void api.qbitStartAutomation().then(loadAll)}
                    className="rounded-md border px-3 py-2 text-sm hover:bg-muted disabled:opacity-50"
                  >
                    {t("quant.startScheduler", { defaultValue: "启动调度" })}
                  </button>
                  <button
                    type="button"
                    onClick={() => void api.qbitStopAutomation().then(loadAll)}
                    className="inline-flex items-center justify-center gap-1 rounded-md border px-3 py-2 text-sm hover:bg-muted"
                  >
                    <Square className="h-3.5 w-3.5" />
                    {t("quant.stopScheduler", { defaultValue: "停止调度" })}
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleTradingToggle()}
                    className="rounded-md border px-3 py-2 text-sm hover:bg-muted"
                  >
                    {ops?.tradingEnabled
                      ? t("quant.disableTrading", { defaultValue: "禁用交易" })
                      : t("quant.enableTrading", { defaultValue: "启用交易" })}
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleKillSwitch()}
                    className={cn(
                      "inline-flex items-center justify-center gap-1 rounded-md border px-3 py-2 text-sm",
                      haltActive
                        ? "border-rose-500/50 bg-rose-500/10 text-rose-600"
                        : "hover:bg-muted",
                    )}
                  >
                    <ShieldAlert className="h-3.5 w-3.5" />
                    {haltActive
                      ? t("quant.killSwitchOn", { defaultValue: "解除急停" })
                      : t("quant.killSwitchOff", { defaultValue: "急停 Kill Switch" })}
                  </button>
                </div>
                {haltActive && haltDetailText ? (
                  <p className="text-[11px] leading-relaxed text-muted-foreground">
                    {haltDetailText}
                  </p>
                ) : null}
                <div className="mt-1 flex items-center gap-2 rounded-md border px-2 py-1.5">
                  <Wallet className="h-4 w-4 text-muted-foreground" />
                  <input
                    type="number"
                    value={resetCash}
                    onChange={(e) => setResetCash(Number(e.target.value))}
                    className="w-full bg-transparent text-sm outline-none"
                  />
                  <button
                    type="button"
                    onClick={() => {
                      if (!window.confirm(t("quant.resetConfirm", { defaultValue: "重置账本会清空持仓与成交，确定？" }))) {
                        return;
                      }
                      void api.qbitResetLedger(resetCash).then(() => {
                        setCycleResult(null);
                        setNotice(t("quant.ledgerReset", { defaultValue: "账本已重置" }));
                        return loadAll();
                      });
                    }}
                    className="inline-flex shrink-0 items-center gap-1 text-xs text-primary hover:underline"
                  >
                    <RotateCcw className="h-3 w-3" />
                    {t("quant.resetLedger", { defaultValue: "重置账本" })}
                  </button>
                </div>
              </div>

              <dl className="mt-4 space-y-2 text-xs text-muted-foreground">
                <div className="flex justify-between gap-2">
                  <dt>{t("quant.rebalanceWindow", { defaultValue: "再平衡窗口" })}</dt>
                  <dd className="tabular-nums">
                    {automation?.rebalance_window?.time_local ?? "14:50"} ·{" "}
                    {automation?.rebalance_window?.every_trading_days ?? 15}
                    {t("quant.days", { defaultValue: " 交易日一次" })}
                  </dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt>{t("quant.initialCash", { defaultValue: "初始资金" })}</dt>
                  <dd className="tabular-nums">
                    {automation?.initial_cash != null ? fmtMoney(automation.initial_cash) : "—"}
                  </dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt>{t("quant.lastRun", { defaultValue: "上次运行" })}</dt>
                  <dd className="truncate tabular-nums">
                    {automation?.last_run_at
                      ? new Date(automation.last_run_at).toLocaleString("zh-CN", { hour12: false })
                      : "—"}
                  </dd>
                </div>
              </dl>
            </section>
          </div>

          <TradingCycleAgentLog
            log={
              cycleResult?.agent_log
                ? {
                    as_of: cycleResult.as_of,
                    ok: cycleResult.agent_log_ok ?? false,
                    text: cycleResult.agent_log,
                    tool_steps: cycleResult.agent_tool_steps,
                  }
                : automation?.last_agent_log
            }
            enabled={automation?.agent_log_enabled}
          />

          {/* Positions — Chinese name first */}
          <ModuleCopilot
            kind="quant"
            title={t("quant.agentOps", { defaultValue: "Agent 交易运营分析" })}
            autoRun={false}
            payload={{
              strategies: automation?.strategies ?? [],
              positions: (snapshot?.positions ?? []).map((p) => ({
                symbol: p.symbol,
                name: p.name,
                quantity: p.quantity,
                avg_cost: p.avg_cost,
                last_price: p.last_price,
              })),
              orders: cycleResult?.orders ?? [],
            }}
          />
          <section className="rounded-xl border bg-card">
            <header className="flex items-center justify-between border-b px-4 py-3">
              <div>
                <h2 className="text-sm font-semibold">
                  {t("quant.positions", { defaultValue: "持仓" })}
                </h2>
                <p className="mt-0.5 text-[11px] text-muted-foreground">
                  {t("quant.positionsNote", {
                    defaultValue: "股票中文名 · 成本 / 现价 · 市值与盈亏",
                  })}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setShowTrades((v) => !v)}
                className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              >
                {showTrades ? (
                  <ChevronDown className="h-3.5 w-3.5" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5" />
                )}
                {t("quant.toggleTrades", { defaultValue: "执行记录" })}
                {trades.length ? ` (${trades.length})` : ""}
              </button>
            </header>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-[11px] text-muted-foreground">
                    <th className="px-4 py-2 font-medium">
                      {t("quant.name", { defaultValue: "名称" })}
                    </th>
                    <th className="px-3 py-2 text-right font-medium">
                      {t("quant.qty", { defaultValue: "数量" })}
                    </th>
                    <th className="px-3 py-2 text-right font-medium">
                      {t("quant.avgCost", { defaultValue: "成本" })}
                    </th>
                    <th className="px-3 py-2 text-right font-medium">
                      {t("quant.lastPrice", { defaultValue: "现价" })}
                    </th>
                    <th className="px-3 py-2 text-right font-medium">
                      {t("quant.marketValue", { defaultValue: "市值" })}
                    </th>
                    <th className="px-4 py-2 text-right font-medium">
                      {t("quant.posPnl", { defaultValue: "持仓盈亏" })}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {(snapshot?.positions ?? []).map((p) => {
                    const avg = p.avg_cost ?? null;
                    const last = p.last_price ?? null;
                    const mv = p.market_value ?? (last != null ? last * p.quantity : null);
                    const posPnl =
                      avg != null && mv != null ? mv - avg * p.quantity : null;
                    return (
                      <tr key={p.symbol} className="border-b border-border/40 hover:bg-muted/20">
                        <td className="px-4 py-2.5">
                          <div className="font-medium">{displayName(p.name, p.symbol)}</div>
                          <div className="font-mono text-[10px] text-muted-foreground">
                            {p.symbol}
                          </div>
                        </td>
                        <td className="px-3 py-2.5 text-right tabular-nums">{p.quantity}</td>
                        <td className="px-3 py-2.5 text-right tabular-nums">
                          {avg == null ? "—" : fmtNum(avg)}
                        </td>
                        <td className="px-3 py-2.5 text-right tabular-nums">
                          {last == null ? "—" : fmtNum(last)}
                        </td>
                        <td className="px-3 py-2.5 text-right tabular-nums">
                          {mv == null ? "—" : fmtMoney(mv)}
                        </td>
                        <td
                          className={cn(
                            "px-4 py-2.5 text-right font-medium tabular-nums",
                            posPnl == null
                              ? "text-muted-foreground"
                              : posPnl >= 0
                                ? "text-rose-500"
                                : "text-emerald-500",
                          )}
                        >
                          {posPnl == null
                            ? "—"
                            : `${posPnl >= 0 ? "+" : ""}${fmtMoney(posPnl)}`}
                        </td>
                      </tr>
                    );
                  })}
                  {!snapshot?.positions?.length ? (
                    <tr>
                      <td colSpan={6} className="px-4 py-8 text-center text-sm text-muted-foreground">
                        {t("quant.noPositions", {
                          defaultValue: "暂无持仓。点「立即跑一轮」按当前策略建仓。",
                        })}
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>

            {showTrades ? (
              <div className="border-t bg-muted/10 px-4 py-3">
                <h3 className="mb-2 text-xs font-medium text-muted-foreground">
                  {t("quant.tradeLog", { defaultValue: "执行记录（策略成交）" })}
                </h3>
                <div className="max-h-64 overflow-y-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-left text-muted-foreground">
                        <th className="py-1 pr-2 font-medium">
                          {t("quant.time", { defaultValue: "时间" })}
                        </th>
                        <th className="py-1 pr-2 font-medium">
                          {t("quant.name", { defaultValue: "名称" })}
                        </th>
                        <th className="py-1 pr-2 font-medium">
                          {t("quant.side", { defaultValue: "方向" })}
                        </th>
                        <th className="py-1 pr-2 text-right font-medium">
                          {t("quant.qty", { defaultValue: "数量" })}
                        </th>
                        <th className="py-1 pr-2 text-right font-medium">
                          {t("quant.price", { defaultValue: "价格" })}
                        </th>
                        <th className="py-1 font-medium">
                          {t("quant.strategy", { defaultValue: "策略" })}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {trades.map((tr, i) => (
                        <tr key={`${tr.ts}-${tr.symbol}-${i}`} className="border-t border-border/40">
                          <td className="py-1.5 pr-2 text-muted-foreground">
                            {new Date(tr.ts).toLocaleString("zh-CN", { hour12: false })}
                          </td>
                          <td className="py-1.5 pr-2">
                            <span className="font-medium">{displayName(tr.name, tr.symbol)}</span>
                            <span className="ml-1 font-mono text-[10px] text-muted-foreground">
                              {tr.symbol}
                            </span>
                          </td>
                          <td
                            className={cn(
                              "py-1.5 pr-2",
                              tr.side === "BUY" ? "text-rose-500" : "text-emerald-500",
                            )}
                          >
                            {tr.side === "BUY"
                              ? t("quant.buy", { defaultValue: "买入" })
                              : t("quant.sell", { defaultValue: "卖出" })}
                          </td>
                          <td className="py-1.5 pr-2 text-right tabular-nums">{tr.quantity}</td>
                          <td className="py-1.5 pr-2 text-right tabular-nums">
                            {fmtNum(tr.price)}
                          </td>
                          <td className="py-1.5 text-muted-foreground">
                            {tr.strategy_id || "—"}
                          </td>
                        </tr>
                      ))}
                      {!trades.length ? (
                        <tr>
                          <td colSpan={6} className="py-4 text-center text-muted-foreground">
                            {t("quant.noTrades", { defaultValue: "暂无成交" })}
                          </td>
                        </tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}
          </section>

          {/* Cycle order detail if any */}
          {cycleResult?.orders?.length ? (
            <section className="rounded-xl border bg-card p-4">
              <h2 className="mb-2 text-sm font-semibold">
                {t("quant.cycleOrders", { defaultValue: "本轮指令" })}
              </h2>
              <div className="flex flex-wrap gap-2">
                {cycleResult.orders.map((o, i) => (
                  <div
                    key={`${o.symbol}-${i}`}
                    className={cn(
                      "rounded-lg border px-3 py-2 text-xs",
                      o.status === "filled"
                        ? "border-emerald-500/30 bg-emerald-500/5"
                        : o.status === "skipped"
                          ? "border-amber-500/30 bg-amber-500/5"
                          : "border-rose-500/30 bg-rose-500/5",
                    )}
                  >
                    <div className="font-medium">
                      {displayName(o.name, o.symbol)}
                      <span className="ml-1 font-mono text-[10px] opacity-70">{o.symbol}</span>
                    </div>
                    <div className="mt-0.5">
                      {o.side === "BUY"
                        ? t("quant.buy", { defaultValue: "买入" })
                        : t("quant.sell", { defaultValue: "卖出" })}
                      {` ${o.quantity} @ ${fmtNum(o.price)}`}
                    </div>
                    <div className="mt-0.5 opacity-80">
                      {o.strategy_id ? `${o.strategy_id} · ` : ""}
                      {o.status}
                      {o.skip_reason ? ` · ${o.skip_reason}` : ""}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ) : null}
        </div>
      ) : null}

      {pane === "strategies" ? (
        <section className="flex flex-col gap-4">
          <div className="rounded-xl border bg-card p-4">
            <h2 className="text-sm font-semibold">
              {t("quant.strategyRepo", { defaultValue: "选择策略" })}
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              {t("quant.strategyRepoNote", {
                defaultValue:
                  "点一张卡片即可切换当前策略（只会启用一个）。切换后点「立即跑一轮」立即按新策略下单。",
              })}
            </p>
            <div className="mt-4 grid gap-3 md:grid-cols-3">
              {(automation?.strategies ?? []).map((s) => {
                const active = s.enabled;
                return (
                  <button
                    key={String(s.strategy_id)}
                    type="button"
                    disabled={selectingStrategy !== null}
                    onClick={() => void handleSelectStrategy(String(s.strategy_id))}
                    className={cn(
                      "rounded-xl border p-4 text-left transition hover:border-primary/50",
                      active
                        ? "border-primary bg-primary/5 shadow-sm"
                        : "bg-background/40",
                      selectingStrategy === String(s.strategy_id) && "opacity-70",
                    )}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="text-sm font-semibold">
                          {String(s.strategy_id)}
                        </div>
                        <div className="mt-0.5 text-[11px] text-muted-foreground">
                          {s.mode === "mean_reversion"
                            ? t("quant.modeMeanRev", { defaultValue: "均值回归" })
                            : s.mode === "equal_weight"
                              ? t("quant.modeEqual", { defaultValue: "等权配置" })
                              : s.mode === "low_volatility"
                                ? t("quant.modeLowVol", { defaultValue: "低波动" })
                                : s.mode === "strong_hand"
                                  ? t("quant.modeStrong", { defaultValue: "强势股" })
                                  : s.mode === "dip_buy"
                                    ? t("quant.modeDip", { defaultValue: "超跌反弹" })
                                    : t("quant.modeMomentum", { defaultValue: "日内动量" })}
                          {` · Top ${Number(s.top_n ?? 5)} · 池 ${Number(s.universe_size ?? 0)}`}
                        </div>
                      </div>
                      <span
                        className={cn(
                          "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium",
                          active
                            ? "bg-primary text-primary-foreground"
                            : "bg-muted text-muted-foreground",
                        )}
                      >
                        {active
                          ? t("quant.inUse", { defaultValue: "使用中" })
                          : t("quant.clickUse", { defaultValue: "点击启用" })}
                      </span>
                    </div>
                    <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
                      {String(s.rule || "")}
                    </p>
                  </button>
                );
              })}
            </div>
            {!automation?.strategies?.length ? (
              <p className="mt-3 text-sm text-muted-foreground">—</p>
            ) : null}

            {/* Parameter panel for the active strategy */}
            {selectedStrategy ? (
              <div className="mt-4 rounded-lg border bg-background/40 p-3">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <h3 className="text-sm font-semibold">
                    {t("quant.params", { defaultValue: "策略参数" })}
                  </h3>
                  <span className="text-xs text-muted-foreground">
                    {String(selectedStrategy.strategy_id)}
                  </span>
                </div>
                <div className="flex flex-wrap items-end gap-3">
                  <label className="text-xs">
                    {t("quant.mode", { defaultValue: "模式" })}
                    <select
                      value={modeDraft}
                      onChange={(e) => setModeDraft(e.target.value)}
                      className="mt-1 block rounded-md border bg-background px-2 py-1.5 text-sm"
                    >
                      <option value="momentum">日内动量</option>
                      <option value="mean_reversion">均值回归</option>
                      <option value="equal_weight">等权配置</option>
                      <option value="low_volatility">低波动</option>
                      <option value="strong_hand">强势股</option>
                      <option value="dip_buy">超跌反弹</option>
                    </select>
                  </label>
                  <label className="text-xs">
                    Top N
                    <input
                      type="number"
                      min={1}
                      max={20}
                      value={topNDraft}
                      onChange={(e) => setTopNDraft(Number(e.target.value))}
                      className="mt-1 w-20 rounded-md border bg-background px-2 py-1.5 text-sm"
                    />
                  </label>
                  <button
                    type="button"
                    onClick={() => void handleSaveParams()}
                    className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"
                  >
                    {t("quant.saveParams", { defaultValue: "保存参数" })}
                  </button>
                </div>
              </div>
            ) : null}
          </div>

          {/* Agent strategy generator */}
          <div className="rounded-xl border bg-card p-4">
            <h2 className="text-sm font-semibold">
              {t("quant.strategyGen", { defaultValue: "Agent 生成策略" })}
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              {t("quant.strategyGenHint", {
                defaultValue:
                  "用一句话描述你要的策略，例如「只买今天跌最多的 3 只银行股」或「低波动等权持有 4 只」。生成后可复制到配置或交给 Agent 继续改。",
              })}
            </p>
            <div className="mt-3 flex flex-col gap-2">
              <textarea
                value={genIntent}
                onChange={(e) => setGenIntent(e.target.value)}
                rows={3}
                placeholder={t("quant.strategyGenPlaceholder", {
                  defaultValue: "描述你的选股/调仓规则…",
                })}
                className="w-full rounded-md border bg-background px-3 py-2 text-sm"
              />
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={genLoading || !genIntent.trim()}
                  onClick={() => void handleGenerateStrategy()}
                  className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
                >
                  {genLoading ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Zap className="h-4 w-4" />
                  )}
                  {t("quant.generate", { defaultValue: "生成策略配置" })}
                </button>
              </div>
              {genText ? (
                <pre className="max-h-64 overflow-auto rounded-md bg-muted/30 p-3 text-[11px] leading-relaxed">
                  {genText}
                </pre>
              ) : null}
            </div>
          </div>

          {strategies.length ? (
            <div className="rounded-xl border bg-card p-4">
              <h3 className="mb-2 text-sm font-semibold">
                {t("quant.registry", { defaultValue: "策略注册表（原始）" })}
              </h3>
              <pre className="max-h-72 overflow-auto rounded-md bg-muted/30 p-3 text-[11px] leading-relaxed">
                {JSON.stringify(strategies, null, 2)}
              </pre>
            </div>
          ) : null}
        </section>
      ) : null}

      {pane === "backtest" ? (
        <section className="rounded-xl border bg-card p-4">
          <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold">
            <BarChart3 className="h-4 w-4 text-primary" />
            {t("quant.quickBacktest", { defaultValue: "SMA 回测验证" })}
          </h2>
          <p className="mb-3 text-xs text-muted-foreground">
            {t("quant.backtestNote", {
              defaultValue:
                "先用历史数据验证逻辑，再交给自动调度。回测通过不代表未来收益，但能避免明显错误的参数。",
            })}
          </p>
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
            <>
              <pre className="mt-4 max-h-80 overflow-auto rounded-md bg-muted/40 p-3 text-[11px] leading-relaxed">
                {JSON.stringify(btResult, null, 2)}
              </pre>
              <ModuleCopilot
                kind="backtest"
                className="mt-4"
                title={t("quant.backtestInsight", { defaultValue: "Agent 回测解读" })}
                payload={{
                  metrics: btResult,
                  symbol: btSymbol,
                  start: btStart,
                  end: btEnd,
                  params: { short_window: btShort, long_window: btLong },
                }}
              />
            </>
          ) : null}
        </section>
      ) : null}

      {pane === "monitor" ? (
        <section className="grid gap-4 md:grid-cols-2">
          <div className="rounded-xl border bg-card p-4">
            <h2 className="mb-3 text-sm font-semibold">
              {t("quant.metrics", { defaultValue: "运行指标" })}
            </h2>
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
            <h2 className="mb-3 text-sm font-semibold">
              {t("quant.alerts", { defaultValue: "告警" })}
            </h2>
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
    </div>
  );
}
