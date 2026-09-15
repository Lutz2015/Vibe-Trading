import { Cpu } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { LLMSettings } from "@/lib/api";

interface Props {
  settings: LLMSettings | null;
  runtimeProvider?: string;
  runtimeModel?: string;
  runtimeReasoningEffort?: string;
  /** When true, render as a slim footer under the composer (no top border). */
  variant?: "bar" | "footer";
}

export function ModelRuntimeBar({
  settings,
  runtimeProvider,
  runtimeModel,
  runtimeReasoningEffort,
  variant = "bar",
}: Props) {
  const { t } = useTranslation();
  if (!settings) return null;

  const providerId = runtimeProvider || settings.provider;
  const provider = settings.providers.find((item) => item.name === providerId);
  const providerLabel = provider?.label || providerId || t("agent.unknownProvider");
  const model = runtimeModel || settings.model_name || t("agent.unknownModel");
  const effortLabels: Record<string, string> = {
    none: t("settings.reasoningEffortNone"),
    low: t("settings.reasoningEffortLow"),
    medium: t("settings.reasoningEffortMedium"),
    high: t("settings.reasoningEffortHigh"),
    max: t("settings.reasoningEffortMax"),
  };
  const reasoningEffort = runtimeReasoningEffort !== undefined
    ? runtimeReasoningEffort
    : settings.reasoning_effort;
  const effortLabel = effortLabels[reasoningEffort] || t("settings.providerDefault");

  const shell = variant === "footer"
    ? "mt-2 flex items-center gap-2 overflow-hidden text-[11px] text-muted-foreground"
    : "shrink-0 border-b border-border/70 bg-background/95 px-6 py-2 backdrop-blur-sm";
  const inner = variant === "footer"
    ? ""
    : "mx-auto flex max-w-3xl items-center gap-2 overflow-hidden text-xs";

  if (variant === "footer") {
    return (
      <div className={shell} aria-label={t("agent.reasoningStrength")}>
        <span className="relative flex h-1.5 w-1.5 shrink-0" aria-hidden="true">
          <span className="absolute inline-flex h-full w-full rounded-full bg-success/30" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-success" />
        </span>
        <span className="shrink-0 font-medium text-foreground/80">{providerLabel}</span>
        <span className="text-muted-foreground/50">·</span>
        <span className="truncate font-mono text-[11px]" title={model}>{model}</span>
        <span className="ms-auto inline-flex shrink-0 items-center gap-1 rounded-full border border-border/60 bg-muted/30 px-2 py-0.5 text-[10px]">
          <Cpu className="h-3 w-3" aria-hidden="true" />
          {t("agent.reasoningStrength")}: {effortLabel}
        </span>
      </div>
    );
  }

  return (
    <div className={shell}>
      <div className={inner}>
        <span className="relative flex h-2 w-2 shrink-0" aria-hidden="true">
          <span className="absolute inline-flex h-full w-full rounded-full bg-success/30" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-success" />
        </span>
        <span className="shrink-0 font-medium text-foreground">{providerLabel}</span>
        <span className="text-muted-foreground/60">·</span>
        <span className="truncate font-mono text-[11px] text-muted-foreground" title={model}>{model}</span>
        <span className="ml-auto inline-flex shrink-0 items-center gap-1.5 rounded-full border border-border/70 bg-muted/35 px-2 py-0.5 text-[10px] text-muted-foreground">
          <Cpu className="h-3 w-3" aria-hidden="true" />
          {t("agent.reasoningStrength")}: {effortLabel}
        </span>
      </div>
    </div>
  );
}
