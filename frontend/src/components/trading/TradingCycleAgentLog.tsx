import { Bot, Wrench } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import type { QbitAgentLog } from "@/lib/api";

interface Props {
  log: QbitAgentLog | null | undefined;
  enabled?: boolean;
  className?: string;
}

export function TradingCycleAgentLog({ log, enabled, className }: Props) {
  const { t } = useTranslation();

  if (!enabled) {
    return (
      <section className={cn("rounded-xl border border-dashed bg-muted/20 px-4 py-3 text-sm text-muted-foreground", className)}>
        {t("tradingCenter.agentLogDisabled")}
      </section>
    );
  }

  if (!log?.text) {
    return (
      <section className={cn("rounded-xl border border-dashed bg-muted/20 px-4 py-3 text-sm text-muted-foreground", className)}>
        {t("tradingCenter.agentLogEmpty")}
      </section>
    );
  }

  return (
    <section className={cn("rounded-xl border border-primary/20 bg-primary/5 shadow-sm", className)}>
      <header className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
        <Bot className="h-4 w-4 text-primary" />
        <h2 className="text-sm font-semibold">{t("tradingCenter.agentLogTitle")}</h2>
        {log.rebalance_id ? (
          <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
            {log.rebalance_id}
          </span>
        ) : null}
        <span
          className={cn(
            "ms-auto rounded-full px-2 py-0.5 text-[10px]",
            log.ok ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "bg-amber-500/10 text-amber-700",
          )}
        >
          {log.skipped ? t("tradingCenter.agentLogSkipped") : log.ok ? t("tradingCenter.agentLogOk") : t("tradingCenter.agentLogFailed")}
        </span>
      </header>
      <div className="px-4 py-3">
        {log.tool_steps && log.tool_steps.length > 0 ? (
          <ul className="mb-3 space-y-1 rounded-md border bg-background/60 px-3 py-2 text-xs">
            {log.tool_steps.map((step, idx) => (
              <li key={`${step.name}-${idx}`} className="flex items-start gap-2">
                <Wrench
                  className={cn(
                    "mt-0.5 h-3 w-3 shrink-0",
                    step.ok ? "text-emerald-600" : "text-amber-600",
                  )}
                />
                <span>
                  <span className="font-medium">{step.name}</span>
                  {step.summary ? (
                    <span className="text-muted-foreground"> — {step.summary}</span>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
        <div className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/90">{log.text}</div>
        {log.error ? (
          <p className="mt-2 text-xs text-amber-600 dark:text-amber-300">{log.error}</p>
        ) : null}
        {log.as_of ? (
          <p className="mt-2 text-[11px] text-muted-foreground">
            {new Date(log.as_of).toLocaleString(undefined, { hour12: false })}
          </p>
        ) : null}
      </div>
    </section>
  );
}
