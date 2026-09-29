import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { StrategyYamlImportDialog } from "@/components/strategy/StrategyYamlImportDialog";
import { useTranslation } from "react-i18next";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Gauge,
  Loader2,
  Play,
  RefreshCw,
  ShieldAlert,
  Square,
  Wallet,
  Zap,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { ModuleCopilot } from "@/components/common/ModuleCopilot";
import { TradingCycleAgentLog } from "@/components/trading/TradingCycleAgentLog";
import {
  api,
  type DataCapabilitiesResponse,
  type LiveStatus,
  type QbitAgentLog,
  type QbitAutomationStatus,
  type QbitLedgerSnapshot,
  type QbitOpsStatus,
} from "@/lib/api";
import { downloadText } from "@/lib/downloadText";
import { formatHaltDetail, isHaltActive } from "@/lib/haltStatus";
import { formatLiveGateReasons, isLiveGateBlocked } from "@/lib/liveGate";

function fmtMoney(value: number): string {
  return value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function TradingCenter() {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runningCycle, setRunningCycle] = useState(false);

  const [automation, setAutomation] = useState<QbitAutomationStatus | null>(null);
  const [ops, setOps] = useState<QbitOpsStatus | null>(null);
  const [snapshot, setSnapshot] = useState<QbitLedgerSnapshot | null>(null);
  const [liveStatus, setLiveStatus] = useState<LiveStatus | null>(null);
  const [dataCap, setDataCap] = useState<DataCapabilitiesResponse | null>(null);
  const [yamlImportOpen, setYamlImportOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [lastCycleLog, setLastCycleLog] = useState<QbitAgentLog | null>(null);

  const loadAll = useCallback(async (mode: "initial" | "refresh" = "refresh") => {
    if (mode === "initial") setLoading(true);
    else setRefreshing(true);
    setError(null);
    try {
      const [automationData, opsData, snapshotData, live, cap] = await Promise.all([
        api.qbitAutomationStatus(),
        api.qbitOpsStatus(),
        api.qbitLedgerSnapshot(),
        api.getLiveStatus().catch(() => null),
        api.dataCapabilities().catch(() => null),
      ]);
      setAutomation(automationData);
      setOps(opsData);
      setSnapshot(snapshotData);
      setLiveStatus(live);
      setDataCap(cap);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("tradingCenter.unavailable"));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [t]);

  useEffect(() => {
    void loadAll("initial");
    const id = window.setInterval(() => void loadAll("refresh"), 60_000);
    return () => window.clearInterval(id);
  }, [loadAll]);

  const pnl = useMemo(() => {
    if (!snapshot) return 0;
    return snapshot.portfolio_value - snapshot.initial_cash;
  }, [snapshot]);

  const pnlPct = useMemo(() => {
    if (!snapshot || snapshot.initial_cash <= 0) return 0;
    return (pnl / snapshot.initial_cash) * 100;
  }, [pnl, snapshot]);

  const activeStrategy = useMemo(
    () => (automation?.strategies ?? []).find((s) => s.enabled) ?? automation?.strategies?.[0],
    [automation],
  );

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

  const handleRunCycle = async () => {
    setRunningCycle(true);
    setError(null);
    try {
      const cycle = await api.qbitRunCycle(true);
      if (cycle.agent_log) {
        setLastCycleLog({
          as_of: cycle.as_of,
          ok: cycle.agent_log_ok ?? false,
          text: cycle.agent_log,
          tool_steps: cycle.agent_tool_steps,
        });
      }
      await loadAll("refresh");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("tradingCenter.cycleFailed"));
    } finally {
      setRunningCycle(false);
    }
  };

  const handleExportYaml = async () => {
    const sid = activeStrategy?.strategy_id;
    if (!sid) return;
    try {
      const res = await api.exportQbitStrategyYaml(String(sid));
      downloadText(res.yaml, `${res.strategy_id}.yaml`, "text/yaml;charset=utf-8");
      setNotice(t("strategyYaml.exportSuccess"));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("strategyYaml.exportFailed"));
    }
  };

  const handleAutomationToggle = async () => {
    try {
      if (automation?.scheduler_running) await api.qbitStopAutomation();
      else await api.qbitStartAutomation();
      await loadAll("refresh");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("tradingCenter.toggleFailed"));
    }
  };

  const insightPayload = useMemo(
    () => ({
      mode: automation?.mode ?? "paper",
      running: automation?.scheduler_running ?? false,
      strategy: activeStrategy?.strategy_id ?? null,
      portfolio_value: snapshot?.portfolio_value ?? null,
      pnl_pct: pnlPct,
      kill_switch: ops?.killSwitch ?? false,
      live_halted: liveStatus?.global_halted ?? false,
    }),
    [automation, activeStrategy, snapshot, pnlPct, ops, liveStatus],
  );

  if (loading) {
    return (
      <div className="flex h-[60vh] items-center justify-center text-muted-foreground">
        <Loader2 className="me-2 h-5 w-5 animate-spin" />
        {t("tradingCenter.loading")}
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5 p-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Gauge className="h-6 w-6 text-primary" />
            <h1 className="text-2xl font-semibold tracking-tight">{t("tradingCenter.title")}</h1>
          </div>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{t("tradingCenter.subtitle")}</p>
        </div>
        <button
          type="button"
          onClick={() => void loadAll("refresh")}
          disabled={refreshing}
          className="inline-flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm hover:bg-muted/50"
        >
          <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />
          {t("tradingCenter.refresh")}
        </button>
      </header>

      <StrategyYamlImportDialog
        open={yamlImportOpen}
        onClose={() => setYamlImportOpen(false)}
        onImported={(msg) => {
          setNotice(msg);
          void loadAll("refresh");
        }}
      />

      {notice ? (
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 px-4 py-3 text-sm text-emerald-700 dark:text-emerald-300">
          {notice}
        </div>
      ) : null}

      {error ? (
        <div className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          {error}
        </div>
      ) : null}

      {liveGateBlocked ? (
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-800 dark:text-amber-200">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <div className="font-medium">{t("liveGate.blockedTitle")}</div>
            <div className="mt-0.5 text-xs opacity-90">{liveGateReasonText}</div>
            <div className="mt-1 text-xs opacity-80">{t("liveGate.blockedHint")}</div>
          </div>
        </div>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label={t("tradingCenter.portfolioValue")}
          value={snapshot ? fmtMoney(snapshot.portfolio_value) : "—"}
          sub={snapshot ? `${t("tradingCenter.cash")} ${fmtMoney(snapshot.cash)}` : undefined}
        />
        <StatCard
          label={t("tradingCenter.pnl")}
          value={snapshot ? `${pnl >= 0 ? "+" : ""}${fmtMoney(pnl)}` : "—"}
          sub={snapshot ? `${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(2)}%` : undefined}
          tone={pnl >= 0 ? "up" : "down"}
        />
        <StatCard
          label={t("tradingCenter.activeStrategy")}
          value={activeStrategy?.rule || activeStrategy?.strategy_id || t("tradingCenter.none")}
          sub={`${automation?.mode ?? "paper"} · ${automation?.scheduler_running ? t("tradingCenter.running") : t("tradingCenter.stopped")}`}
        />
        <StatCard
          label={t("tradingCenter.dataProvider")}
          value={dataCap?.tushare.connected ? "Tushare" : t("tradingCenter.fallback")}
          sub={dataCap?.tushare.message ?? t("tradingCenter.dataUnknown")}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <section className="rounded-xl border border-border/60 bg-card p-4 lg:col-span-2">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="font-medium">{t("tradingCenter.automation")}</h2>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void handleAutomationToggle()}
                disabled={liveGateBlocked && !automation?.scheduler_running}
                className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50"
              >
                {automation?.scheduler_running ? <Square className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                {automation?.scheduler_running ? t("tradingCenter.stop") : t("tradingCenter.start")}
              </button>
              <button
                type="button"
                onClick={() => void handleRunCycle()}
                disabled={runningCycle || liveGateBlocked}
                className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-sm disabled:opacity-50"
              >
                {runningCycle ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Zap className="h-3.5 w-3.5" />}
                {t("tradingCenter.runOnce")}
              </button>
              {activeStrategy?.strategy_id ? (
                <button
                  type="button"
                  onClick={() => void handleExportYaml()}
                  className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-sm"
                >
                  {t("strategyYaml.exportYaml")}
                </button>
              ) : null}
            </div>
          </div>

          <StatusPill
            icon={ShieldAlert}
            label={t("tradingCenter.haltStatus")}
            ok={!haltActive}
            okText={t("tradingCenter.normal")}
            badText={t("tradingCenter.halted")}
            detail={haltDetailText}
          />

          {snapshot && snapshot.positions.length > 0 ? (
            <div className="mt-4 overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border/60 text-muted-foreground">
                    <th className="py-2 text-start font-medium">{t("tradingCenter.symbol")}</th>
                    <th className="py-2 text-end font-medium">{t("tradingCenter.qty")}</th>
                    <th className="py-2 text-end font-medium">{t("tradingCenter.value")}</th>
                  </tr>
                </thead>
                <tbody>
                  {snapshot.positions.slice(0, 8).map((p) => (
                    <tr key={p.symbol} className="border-b border-border/30">
                      <td className="py-2">{p.name?.trim() || p.symbol}</td>
                      <td className="py-2 text-end tabular-nums">{p.quantity}</td>
                      <td className="py-2 text-end tabular-nums">
                        {p.market_value != null ? fmtMoney(p.market_value) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="mt-4 text-sm text-muted-foreground">{t("tradingCenter.noPositions")}</p>
          )}
        </section>

        <section className="flex flex-col gap-3">
          <button
            type="button"
            onClick={() => setYamlImportOpen(true)}
            className="flex items-center gap-3 rounded-lg border border-primary/40 bg-primary/5 px-4 py-3 text-sm text-primary hover:bg-primary/10"
          >
            <Zap className="h-4 w-4" />
            <span className="flex-1 text-start">{t("strategyYaml.importButton")}</span>
          </button>
          <QuickLink to="/strategies" icon={Zap} label={t("tradingCenter.linkStrategies")} />
          <QuickLink to="/quant-desk" icon={Gauge} label={t("tradingCenter.linkQuantDesk")} />
          <QuickLink to="/runtime" icon={Activity} label={t("tradingCenter.linkRuntime")} />
          <QuickLink to="/portfolio" icon={Wallet} label={t("tradingCenter.linkPortfolio")} />
          <QuickLink to="/market" icon={Activity} label={t("tradingCenter.linkMarket")} />
          <QuickLink to="/intelligence" icon={Activity} label={t("layout.intelligence")} />
          <QuickLink to="/news" icon={Activity} label={t("tradingCenter.linkNews")} />
        </section>
      </div>

      <TradingCycleAgentLog
        log={lastCycleLog ?? automation?.last_agent_log}
        enabled={automation?.agent_log_enabled}
      />

      <ModuleCopilot
        kind="quant"
        title={t("tradingCenter.agentBrief")}
        payload={insightPayload}
        autoRun={false}
        runKey={`${activeStrategy?.strategy_id ?? "none"}-${snapshot?.as_of ?? ""}`}
      />
    </div>
  );
}

function StatCard({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "up" | "down";
}) {
  return (
    <div className="rounded-xl border border-border/60 bg-card p-4">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p
        className={cn(
          "mt-1 text-xl font-semibold tabular-nums",
          tone === "up" && "text-emerald-600 dark:text-emerald-400",
          tone === "down" && "text-red-600 dark:text-red-400",
        )}
      >
        {value}
      </p>
      {sub ? <p className="mt-1 text-xs text-muted-foreground">{sub}</p> : null}
    </div>
  );
}

function StatusPill({
  icon: Icon,
  label,
  ok,
  okText,
  badText,
  detail,
}: {
  icon: typeof ShieldAlert;
  label: string;
  ok: boolean;
  okText: string;
  badText: string;
  detail?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border px-3 py-2 text-sm",
        ok ? "border-emerald-500/30 bg-emerald-500/5" : "border-red-500/30 bg-red-500/5",
      )}
    >
      <div className="flex items-center gap-2">
        <Icon className={cn("h-4 w-4 shrink-0", ok ? "text-emerald-600" : "text-red-600")} />
        <span className="text-muted-foreground">{label}</span>
        <span className="ms-auto font-medium">{ok ? okText : badText}</span>
      </div>
      {detail ? (
        <p className="mt-1 ps-6 text-xs text-muted-foreground">{detail}</p>
      ) : null}
    </div>
  );
}

function QuickLink({
  to,
  icon: Icon,
  label,
}: {
  to: string;
  icon: typeof Gauge;
  label: string;
}) {
  return (
    <Link
      to={to}
      className="flex items-center gap-3 rounded-lg border border-border/60 bg-card px-4 py-3 text-sm hover:bg-muted/40"
    >
      <Icon className="h-4 w-4 text-primary" />
      <span className="flex-1">{label}</span>
      <ArrowRight className="h-4 w-4 text-muted-foreground" />
    </Link>
  );
}
