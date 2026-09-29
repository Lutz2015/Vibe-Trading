import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Brain,
  LineChart,
  Loader2,
  Newspaper,
  RefreshCw,
  Thermometer,
  Database,
} from "lucide-react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { ModuleCopilot } from "@/components/common/ModuleCopilot";
import { api, type IntelligenceSnapshot, type NewsArticle } from "@/lib/api";

const TOPICS = [
  { id: "", labelKey: "intelligence.topicAll" },
  { id: "global", labelKey: "intelligence.topicMacro" },
  { id: "ai", labelKey: "intelligence.topicAi" },
  { id: "semiconductor", labelKey: "intelligence.topicSemi" },
  { id: "newenergy", labelKey: "intelligence.topicEnergy" },
] as const;

function signalDot(signal?: string | null) {
  if (signal === "positive") return "bg-rose-500";
  if (signal === "negative") return "bg-emerald-500";
  return "bg-muted-foreground/40";
}

export function IntelligenceCenter() {
  const { t } = useTranslation();
  const [topic, setTopic] = useState("");
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [snapshot, setSnapshot] = useState<IntelligenceSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.fetchIntelligenceSnapshot({
        limit: 16,
        topic: topic || undefined,
      });
      setSnapshot(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("intelligence.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [topic, t]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const id = window.setInterval(() => void load(), 60_000);
    return () => window.clearInterval(id);
  }, [load]);

  const handleSync = async () => {
    setSyncing(true);
    try {
      await api.syncDataCache();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("intelligence.syncFailed"));
    } finally {
      setSyncing(false);
    }
  };

  const articles = (snapshot?.news?.articles ?? []) as NewsArticle[];
  const copilotPayload = useMemo(
    () => ({
      market: snapshot?.market ?? {},
      news: snapshot?.news ?? {},
      sentiment: snapshot?.sentiment ?? {},
      cache: snapshot?.cache ?? {},
    }),
    [snapshot],
  );

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5 p-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Brain className="h-6 w-6 text-primary" />
            <h1 className="text-2xl font-semibold tracking-tight">{t("intelligence.title")}</h1>
          </div>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{t("intelligence.subtitle")}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void handleSync()}
            disabled={syncing}
            className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-muted/50"
          >
            {syncing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
            {t("intelligence.syncCache")}
          </button>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-muted/50"
          >
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
            {t("intelligence.refresh")}
          </button>
        </div>
      </header>

      {error ? (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      ) : null}

      <div className="flex flex-wrap gap-2">
        {TOPICS.map((chip) => (
          <button
            key={chip.id || "all"}
            type="button"
            onClick={() => setTopic(chip.id)}
            className={cn(
              "rounded-full border px-3 py-1 text-xs",
              topic === chip.id ? "border-primary bg-primary/10 text-primary" : "text-muted-foreground",
            )}
          >
            {t(chip.labelKey)}
          </button>
        ))}
        <Link to="/news" className="ms-auto text-xs text-primary hover:underline">
          {t("intelligence.openNewsRadar")}
        </Link>
      </div>

      {loading && !snapshot ? (
        <div className="flex h-40 items-center justify-center text-muted-foreground">
          <Loader2 className="me-2 h-5 w-5 animate-spin" />
          {t("intelligence.loading")}
        </div>
      ) : (
        <>
          <div className="grid gap-4 lg:grid-cols-3">
            <section className="rounded-xl border bg-card p-4 lg:col-span-1">
              <div className="mb-3 flex items-center gap-2 text-sm font-medium">
                <LineChart className="h-4 w-4 text-primary" />
                {t("intelligence.marketStrip")}
              </div>
              <ul className="space-y-1.5 text-sm">
                {(snapshot?.market?.indices ?? []).slice(0, 5).map((idx) => (
                  <li key={idx.name} className="flex justify-between tabular-nums">
                    <span>{idx.name}</span>
                    <span
                      className={cn(
                        (idx.change_pct ?? 0) >= 0 ? "text-emerald-600" : "text-red-600",
                      )}
                    >
                      {idx.change_pct != null ? `${idx.change_pct}%` : "—"}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-[11px] text-muted-foreground">
                {snapshot?.market?.primary_provider ?? "—"} · {snapshot?.market?.as_of ?? snapshot?.as_of}
              </p>
            </section>

            <section className="rounded-xl border bg-card p-4 lg:col-span-1">
              <div className="mb-3 flex items-center gap-2 text-sm font-medium">
                <Thermometer className="h-4 w-4 text-primary" />
                {t("intelligence.sentimentStrip")}
              </div>
              <p className="text-3xl font-semibold tabular-nums">
                {snapshot?.sentiment?.composite ?? "—"}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {snapshot?.sentiment?.mode ?? "—"} · {snapshot?.sentiment?.as_of ?? "—"}
              </p>
              <ul className="mt-3 space-y-1 text-xs text-muted-foreground">
                {(snapshot?.sentiment?.items ?? []).slice(0, 4).map((it) => (
                  <li key={it.title} className="truncate">
                    {it.title} · {it.probability}%
                  </li>
                ))}
              </ul>
            </section>

            <section className="rounded-xl border bg-card p-4 lg:col-span-1">
              <div className="mb-3 flex items-center gap-2 text-sm font-medium">
                <Database className="h-4 w-4 text-primary" />
                {t("intelligence.cacheStrip")}
              </div>
              <dl className="grid grid-cols-2 gap-2 text-sm">
                <div>
                  <dt className="text-muted-foreground">{t("intelligence.bars")}</dt>
                  <dd className="font-medium tabular-nums">{snapshot?.cache?.daily_bars ?? 0}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">{t("intelligence.newsCached")}</dt>
                  <dd className="font-medium tabular-nums">{snapshot?.cache?.news_articles ?? 0}</dd>
                </div>
              </dl>
              <p className="mt-2 truncate text-[10px] text-muted-foreground">
                {snapshot?.cache?.db_path}
              </p>
            </section>
          </div>

          <section className="rounded-xl border bg-card">
            <header className="flex items-center justify-between border-b px-4 py-3">
              <div className="flex items-center gap-2">
                <Newspaper className="h-4 w-4 text-primary" />
                <h2 className="text-sm font-semibold">{t("intelligence.newsFeed")}</h2>
              </div>
              <span className="text-xs text-muted-foreground">
                {t("intelligence.signalSummary", {
                  pos: snapshot?.news?.positive_count ?? 0,
                  neg: snapshot?.news?.negative_count ?? 0,
                })}
              </span>
            </header>
            <ul className="divide-y">
              {articles.length === 0 ? (
                <li className="px-4 py-8 text-center text-sm text-muted-foreground">
                  {t("intelligence.noNews")}
                </li>
              ) : (
                articles.map((a, i) => (
                  <li key={`${a.title}-${i}`} className="flex gap-3 px-4 py-3 text-sm">
                    <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", signalDot(a.signal))} />
                    <div className="min-w-0 flex-1">
                      {a.url ? (
                        <a
                          href={a.url}
                          target="_blank"
                          rel="noreferrer"
                          className="font-medium hover:text-primary"
                        >
                          {a.title}
                        </a>
                      ) : (
                        <p className="font-medium">{a.title}</p>
                      )}
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {a.source ?? "—"} · {a.published ?? "—"}
                      </p>
                    </div>
                  </li>
                ))
              )}
            </ul>
          </section>

          <ModuleCopilot
            kind="intelligence"
            title={t("intelligence.copilotTitle")}
            payload={copilotPayload}
            autoRun={false}
            runKey={snapshot?.as_of ?? "none"}
          />
        </>
      )}
    </div>
  );
}
