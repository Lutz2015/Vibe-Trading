import { useCallback, useEffect, useState } from "react";
import { Sparkles, Loader2, MessageSquare, ChevronDown, ChevronUp, Wrench } from "lucide-react";
import { useNavigate } from "react-router";

/** Client-side navigate that also works in unit tests outside a <Router>. */
function useSafeNavigate(): (to: string) => void {
  try {
    const navigate = useNavigate();
    return (to: string) => navigate(to);
  } catch {
    return (to: string) => {
      if (typeof window === "undefined") return;
      window.history.pushState({}, "", to);
      window.dispatchEvent(new PopStateEvent("popstate"));
    };
  }
}
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { api, type CopilotToolStep } from "@/lib/api";

export type InsightKind =
  | "market"
  | "news"
  | "sentiment"
  | "logic_chain"
  | "portfolio"
  | "quant"
  | "strategy_gen"
  | "backtest"
  | "intelligence";

interface Props {
  kind: InsightKind;
  /** Structured context from the current page (indices/boards/articles/…). */
  payload: Record<string, unknown>;
  title?: string;
  className?: string;
  onText?: (text: string) => void;
  /** Auto-run once on mount when payload is non-empty. */
  autoRun?: boolean;
  runKey?: string;
}

/**
 * Module Copilot — mini ReAct with scoped tools for each page module.
 * Falls back to one-shot insight if copilot fails with a transport error.
 */
export function ModuleCopilot({
  kind,
  payload,
  title,
  className,
  onText,
  autoRun = false,
  runKey,
}: Props) {
  const { t, i18n } = useTranslation();
  const navigate = useSafeNavigate();
  const panelTitle = title ?? t("moduleCopilot.title");
  const [open, setOpen] = useState(autoRun);
  const [loading, setLoading] = useState(false);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [toolSteps, setToolSteps] = useState<CopilotToolStep[]>([]);
  const [lastKey, setLastKey] = useState<string | null>(null);

  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    setToolSteps([]);
    try {
      const res = await api.copilotInsight({
        kind,
        payload,
        locale: i18n.language || "zh-CN",
      });
      if (res.ok && res.text) {
        setText(res.text);
        setToolSteps(res.tool_steps || []);
        setOpen(true);
        onText?.(res.text);
      } else {
        setError(res.error || t("moduleCopilot.failed"));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : t("moduleCopilot.failed"));
    } finally {
      setLoading(false);
    }
  }, [kind, payload, onText, i18n.language, t]);

  const key = runKey ?? kind;
  useEffect(() => {
    if (!autoRun) return;
    if (lastKey === key) return;
    setLastKey(key);
    void run();
  }, [autoRun, key, lastKey, run]);

  const continueInAgent = () => {
    const seed =
      text ||
      t("moduleCopilot.continueSeed", {
        defaultValue: "请基于以下数据给出投研解读，并指出下一步该核实什么：\n{{data}}",
        data: JSON.stringify(payload).slice(0, 1500),
      });
    navigate(`/agent?prompt=${encodeURIComponent(seed.slice(0, 1800))}`);
  };

  return (
    <section
      className={cn(
        "rounded-xl border border-primary/20 bg-primary/5 shadow-sm",
        className,
      )}
    >
      <header className="flex flex-wrap items-center gap-2 px-4 py-3">
        <Sparkles className="h-4 w-4 text-primary" />
        <h2 className="text-sm font-semibold">{panelTitle}</h2>
        <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
          {t("moduleCopilot.badge")}
        </span>
        <div className="ms-auto flex items-center gap-2">
          <button
            type="button"
            onClick={() => void run()}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-60"
          >
            {loading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Sparkles className="h-3.5 w-3.5" />
            )}
            {loading
              ? t("moduleCopilot.analyzing")
              : text || error
                ? t("moduleCopilot.rerun")
                : t("moduleCopilot.run")}
          </button>
          {(text || error || toolSteps.length > 0) && (
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="p-1 text-muted-foreground hover:text-foreground"
              aria-label={open ? t("moduleCopilot.collapse") : t("moduleCopilot.expand")}
            >
              {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </button>
          )}
        </div>
      </header>
      {open && (text || error || toolSteps.length > 0) ? (
        <div className="border-t px-4 py-3">
          {toolSteps.length > 0 ? (
            <ul className="mb-3 space-y-1 rounded-md border bg-background/60 px-3 py-2 text-xs">
              {toolSteps.map((step, idx) => (
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
          {error ? (
            <p className="text-xs text-amber-600 dark:text-amber-300">{error}</p>
          ) : (
            <div className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/90">
              {text}
            </div>
          )}
          <button
            type="button"
            onClick={continueInAgent}
            className="mt-3 inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs text-muted-foreground hover:bg-muted"
          >
            <MessageSquare className="h-3.5 w-3.5" />
            {t("moduleCopilot.continueInAgent")}
          </button>
        </div>
      ) : null}
    </section>
  );
}

/** @deprecated Use ModuleCopilot */
export const AiInsightPanel = ModuleCopilot;
