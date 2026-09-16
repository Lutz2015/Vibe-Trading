import { useCallback, useEffect, useState } from "react";
import { Sparkles, Loader2, MessageSquare, ChevronDown, ChevronUp } from "lucide-react";
import { useNavigate } from "react-router";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";

export type InsightKind = "market" | "news" | "sentiment" | "logic_chain";

interface Props {
  kind: InsightKind;
  /** Structured context from the current page (indices/boards/articles/…). */
  payload: Record<string, unknown>;
  /** Extra instructions, e.g. topic for logic-chain generation. */
  title?: string;
  className?: string;
  onText?: (text: string) => void;
  /** Auto-run once on mount when payload is non-empty. */
  autoRun?: boolean;
  runKey?: string;
}

/**
 * One-shot Agent brief for a module page. Does not open a chat session;
 * "继续对话" hands the draft to the Agent composer via a prompt query param.
 */
export function AiInsightPanel({
  kind,
  payload,
  title = "AI 解读",
  className,
  onText,
  autoRun = false,
  runKey,
}: Props) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(autoRun);
  const [loading, setLoading] = useState(false);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [lastKey, setLastKey] = useState<string | null>(null);

  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.analyzeInsight({ kind, payload, locale: navigator.language || "zh-CN" });
      if (res.ok && res.text) {
        setText(res.text);
        setOpen(true);
        onText?.(res.text);
      } else {
        setError(res.error || "AI 解读失败（请检查 LLM 配置）");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "AI 解读失败");
    } finally {
      setLoading(false);
    }
  }, [kind, payload, onText]);

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
      `请基于以下数据给出投研解读，并指出下一步该核实什么：\n${JSON.stringify(payload).slice(0, 1500)}`;
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
        <h2 className="text-sm font-semibold">{title}</h2>
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
            {loading ? "分析中…" : text || error ? "重新分析" : "AI 解读"}
          </button>
          {(text || error) && (
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="p-1 text-muted-foreground hover:text-foreground"
              aria-label={open ? "收起" : "展开"}
            >
              {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </button>
          )}
        </div>
      </header>
      {open && (text || error) ? (
        <div className="border-t px-4 py-3">
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
            在对话中继续
          </button>
        </div>
      ) : null}
    </section>
  );
}
