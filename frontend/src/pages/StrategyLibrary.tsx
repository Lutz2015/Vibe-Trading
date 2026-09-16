import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  FileCode2,
  FolderKanban,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Save,
  Search,
  Sparkles,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type StrategyItem } from "@/lib/api";

const KINDS = ["通用策略", "选股", "择时", "对冲", "套利"] as const;
const LANGS = [
  { id: "python", label: "Python" },
  { id: "javascript", label: "JavaScript" },
] as const;

const AI_CHIPS = [
  "写一个双均线交易策略",
  "写一个 RSI 超买超卖策略",
  "写一个 MACD 金叉死叉策略",
  "写一个布林带突破策略",
  "写一个网格交易策略",
  "写一个动量突破策略",
  "分析策略逻辑与风险",
  "给代码添加中文注释",
];

function fmtTime(ts?: number): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleString("zh-CN", { hour12: false });
}

function relTime(ts?: number): string {
  if (!ts) return "—";
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

export function StrategyLibrary() {
  const [items, setItems] = useState<StrategyItem[]>([]);
  const [groups, setGroups] = useState<string[]>(["默认"]);
  const [activeGroup, setActiveGroup] = useState("默认");
  const [kindFilter, setKindFilter] = useState("全部种类");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<StrategyItem | null>(null);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const flash = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(null), 2200);
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.listStrategies();
      setItems(res.items || []);
      setGroups(res.groups?.length ? res.groups : ["默认"]);
    } catch (e) {
      flash(e instanceof Error ? e.message : "加载策略库失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return items.filter((s) => {
      if (activeGroup !== "全部" && (s.group || "默认") !== activeGroup) return false;
      if (kindFilter !== "全部种类" && s.kind !== kindFilter) return false;
      if (q && !`${s.name} ${s.description}`.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [items, activeGroup, kindFilter, search]);

  const createStrategy = async () => {
    try {
      const created = await api.createStrategy({
        name: `策略-${items.length + 1}`,
        group: activeGroup === "全部" ? "默认" : activeGroup,
        kind: "通用策略",
        language: "python",
      });
      setItems((prev) => [created, ...prev]);
      setEditing(created);
    } catch (e) {
      flash(e instanceof Error ? e.message : "新建失败");
    }
  };

  const duplicate = async (id: string) => {
    try {
      const clone = await api.duplicateStrategy(id);
      setItems((prev) => [clone, ...prev]);
      flash("已复制");
    } catch (e) {
      flash(e instanceof Error ? e.message : "复制失败");
    }
    setMenuFor(null);
  };

  const remove = async (id: string) => {
    if (!window.confirm("确定删除该策略？此操作不可恢复。")) return;
    try {
      await api.deleteStrategy(id);
      setItems((prev) => prev.filter((s) => s.id !== id));
      if (editing?.id === id) setEditing(null);
      flash("已删除");
    } catch (e) {
      flash(e instanceof Error ? e.message : "删除失败");
    }
    setMenuFor(null);
  };

  const saveItem = async (item: StrategyItem) => {
    try {
      const updated = await api.updateStrategy(item.id, {
        name: item.name,
        group: item.group,
        kind: item.kind,
        language: item.language,
        code: item.code,
        notes: item.notes,
        description: item.description,
      });
      setItems((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
      setEditing(updated);
      flash("已保存");
      return updated;
    } catch (e) {
      flash(e instanceof Error ? e.message : "保存失败");
      return item;
    }
  };

  if (editing) {
    return (
      <StrategyEditor
        item={editing}
        groups={groups}
        onBack={() => {
          void load();
          setEditing(null);
        }}
        onSave={saveItem}
        onFlash={flash}
      />
    );
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <FileCode2 className="h-6 w-6 text-primary" />
          <h1 className="text-2xl font-bold">策略库</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void createStrategy()}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"
          >
            <Plus className="h-4 w-4" />
            新建策略
          </button>
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex items-center gap-1.5 rounded-md border px-3 py-2 text-sm text-muted-foreground hover:bg-muted"
          >
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
            刷新
          </button>
        </div>
      </div>

      <div className="rounded-xl border bg-card shadow-sm">
        {/* Group tabs */}
        <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
          {groups.map((g) => {
            const count = items.filter((s) => (s.group || "默认") === g).length;
            return (
              <button
                key={g}
                type="button"
                onClick={() => setActiveGroup(g)}
                className={cn(
                  "rounded-md px-3 py-1.5 text-sm transition",
                  activeGroup === g
                    ? "bg-primary/10 text-primary font-medium"
                    : "text-muted-foreground hover:bg-muted",
                )}
              >
                {g}
                <span className="ms-1.5 rounded-full bg-muted px-1.5 text-[10px]">
                  {count}
                </span>
              </button>
            );
          })}
          <div className="ms-auto flex items-center gap-2">
            <select
              value={kindFilter}
              onChange={(e) => setKindFilter(e.target.value)}
              className="rounded-md border bg-background px-2 py-1.5 text-xs"
            >
              <option>全部种类</option>
              {KINDS.map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
            <div className="relative">
              <Search className="pointer-events-none absolute start-2 top-2 h-3.5 w-3.5 text-muted-foreground" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜索"
                className="w-40 rounded-md border bg-background py-1.5 pe-2 ps-7 text-xs outline-none focus:border-primary"
              />
            </div>
          </div>
        </div>

        {/* Table */}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-muted-foreground">
                <th className="px-4 py-2 text-start font-medium">名称</th>
                <th className="px-3 py-2 text-end font-medium">最后修改</th>
                <th className="px-3 py-2 text-end font-medium">创建日期</th>
                <th className="px-4 py-2 text-end font-medium">操作项</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((s) => (
                <tr key={s.id} className="border-t hover:bg-muted/20">
                  <td className="px-4 py-3">
                    <button
                      type="button"
                      onClick={() => setEditing(s)}
                      className="inline-flex items-center gap-2 text-start hover:text-primary"
                    >
                      <FileCode2 className="h-4 w-4 text-muted-foreground" />
                      <span className="font-medium">{s.name}</span>
                      <span className="text-[11px] text-muted-foreground">{s.kind}</span>
                    </button>
                  </td>
                  <td className="px-3 py-3 text-end text-xs text-muted-foreground">
                    {relTime(s.updated_at)}
                  </td>
                  <td className="px-3 py-3 text-end font-mono text-xs text-muted-foreground">
                    {fmtTime(s.created_at)}
                  </td>
                  <td className="px-4 py-3 text-end">
                    <div className="inline-flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => setEditing(s)}
                        className="rounded px-2 py-1 text-xs text-sky-500 hover:bg-sky-500/10"
                      >
                        编辑
                      </button>
                      <button
                        type="button"
                        onClick={() => void duplicate(s.id)}
                        className="rounded px-2 py-1 text-xs text-muted-foreground hover:bg-muted"
                      >
                        复制
                      </button>
                      <button
                        type="button"
                        onClick={() => void remove(s.id)}
                        className="rounded px-2 py-1 text-xs text-danger hover:bg-danger/10"
                      >
                        删除
                      </button>
                      <div className="relative">
                        <button
                          type="button"
                          onClick={() => setMenuFor(menuFor === s.id ? null : s.id)}
                          className="rounded px-2 py-1 text-xs text-muted-foreground hover:bg-muted"
                        >
                          <MoreHorizontal className="inline h-3.5 w-3.5" />
                        </button>
                        {menuFor === s.id ? (
                          <div className="absolute end-0 z-20 mt-1 w-28 rounded-md border bg-popover py-1 shadow-lg">
                            {["导出", "运行"].map((label) => (
                              <button
                                key={label}
                                type="button"
                                onClick={() => {
                                  if (label === "导出") {
                                    const blob = new Blob([s.code || ""], {
                                      type: "text/plain;charset=utf-8",
                                    });
                                    const a = document.createElement("a");
                                    a.href = URL.createObjectURL(blob);
                                    a.download = `${s.name || "strategy"}.py`;
                                    a.click();
                                    URL.revokeObjectURL(a.href);
                                    flash("已导出代码");
                                  } else {
                                    flash("可在 Agent 中发送：回测策略 " + s.name);
                                  }
                                  setMenuFor(null);
                                }}
                                className="block w-full px-3 py-1.5 text-start text-xs hover:bg-muted"
                              >
                                {label}
                              </button>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    </div>
                  </td>
                </tr>
              ))}
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-4 py-12 text-center text-sm text-muted-foreground">
                    暂无策略，点击右上角「新建策略」或让 AI 生成。
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>

      {toast ? (
        <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-md bg-foreground px-4 py-2 text-sm text-background shadow-lg">
          {toast}
        </div>
      ) : null}
    </div>
  );
}

function StrategyEditor({
  item,
  groups,
  onBack,
  onSave,
  onFlash,
}: {
  item: StrategyItem;
  groups: string[];
  onBack: () => void;
  onSave: (item: StrategyItem) => Promise<StrategyItem>;
  onFlash: (msg: string) => void;
}) {
  const [draft, setDraft] = useState(item);
  const [tab, setTab] = useState<"code" | "notes" | "description">("code");
  const [aiOpen, setAiOpen] = useState(true);
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiLoading, setAiLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => setDraft(item), [item]);

  const runAi = async (prompt: string, mode: "generate" | "improve" | "explain" = "generate") => {
    if (!prompt.trim()) return;
    setAiLoading(true);
    try {
      const res = await api.strategyAi({
        prompt: prompt.trim(),
        language: draft.language,
        current_code: mode === "generate" ? "" : draft.code,
        mode,
      });
      if (!res.ok) {
        onFlash(res.error || "AI 生成失败");
        return;
      }
      if (mode === "explain") {
        setDraft((d) => ({ ...d, notes: res.code }));
        setTab("notes");
        onFlash("已写入笔记");
      } else {
        setDraft((d) => ({ ...d, code: res.code }));
        setTab("code");
        onFlash(mode === "improve" ? "AI 已优化代码" : "AI 已生成代码");
      }
    } catch (e) {
      onFlash(e instanceof Error ? e.message : "AI 请求失败");
    } finally {
      setAiLoading(false);
    }
  };

  const doSave = async () => {
    setSaving(true);
    await onSave(draft);
    setSaving(false);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b px-4 py-3">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          返回策略列表
        </button>
        <div className="ms-2 flex gap-4 text-sm">
          <span className="border-b-2 border-primary pb-1 font-medium text-primary">策略编辑</span>
          <span className="pb-1 text-muted-foreground">模拟回测</span>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3 border-b px-4 py-3">
        <select
          value={draft.language}
          onChange={(e) => setDraft({ ...draft, language: e.target.value })}
          className="rounded-md border bg-background px-2 py-1.5 text-sm"
        >
          {LANGS.map((l) => (
            <option key={l.id} value={l.id}>
              {l.label}
            </option>
          ))}
        </select>
        <input
          value={draft.name}
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          className="min-w-[160px] flex-1 rounded-md border bg-background px-3 py-1.5 text-sm outline-none focus:border-primary"
          placeholder="策略名称"
        />
        <select
          value={draft.kind}
          onChange={(e) => setDraft({ ...draft, kind: e.target.value })}
          className="rounded-md border bg-background px-2 py-1.5 text-sm"
        >
          {KINDS.map((k) => (
            <option key={k}>{k}</option>
          ))}
        </select>
        <select
          value={draft.group}
          onChange={(e) => setDraft({ ...draft, group: e.target.value })}
          className="rounded-md border bg-background px-2 py-1.5 text-sm"
        >
          {groups.map((g) => (
            <option key={g}>{g}</option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => void doSave()}
          disabled={saving}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-60"
        >
          <Save className="h-4 w-4" />
          {saving ? "保存中…" : "保存"}
        </button>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-center gap-1 border-b px-3 py-2">
            {(
              [
                ["code", "代码"],
                ["notes", "笔记"],
                ["description", "描述"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                onClick={() => setTab(id)}
                className={cn(
                  "rounded px-2.5 py-1 text-xs",
                  tab === id ? "bg-muted font-medium" : "text-muted-foreground",
                )}
              >
                {label}
              </button>
            ))}
            <span className="ms-auto text-[11px] text-muted-foreground">
              <FolderKanban className="me-1 inline h-3 w-3" />
              {draft.group} · {draft.kind}
            </span>
          </div>
          {tab === "code" ? (
            <textarea
              value={draft.code}
              onChange={(e) => setDraft({ ...draft, code: e.target.value })}
              spellCheck={false}
              className="min-h-[360px] flex-1 resize-none bg-background p-4 font-mono text-[13px] leading-relaxed outline-none"
            />
          ) : tab === "notes" ? (
            <textarea
              value={draft.notes}
              onChange={(e) => setDraft({ ...draft, notes: e.target.value })}
              placeholder="策略笔记、调参记录、风险说明…"
              className="min-h-[360px] flex-1 resize-none bg-background p-4 text-sm leading-relaxed outline-none"
            />
          ) : (
            <textarea
              value={draft.description}
              onChange={(e) => setDraft({ ...draft, description: e.target.value })}
              placeholder="策略描述与适用场景…"
              className="min-h-[360px] flex-1 resize-none bg-background p-4 text-sm leading-relaxed outline-none"
            />
          )}
        </div>

        {/* AI assistant */}
        {aiOpen ? (
          <aside className="flex w-[300px] shrink-0 flex-col border-s">
            <header className="flex items-center justify-between border-b px-3 py-2">
              <div className="flex items-center gap-1.5 text-sm font-medium">
                <Sparkles className="h-4 w-4 text-primary" />
                AI 助手
              </div>
              <button type="button" onClick={() => setAiOpen(false)} className="p-1 text-muted-foreground">
                <X className="h-3.5 w-3.5" />
              </button>
            </header>
            <div className="flex-1 space-y-3 overflow-auto p-3">
              <div className="rounded-lg bg-muted/40 px-3 py-4 text-center">
                <div className="text-sm font-semibold">量化策略助手</div>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  策略编写 · 代码优化 · Bug修复 · 回测分析
                </p>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {AI_CHIPS.map((chip) => (
                  <button
                    key={chip}
                    type="button"
                    disabled={aiLoading}
                    onClick={() => void runAi(chip, chip.includes("注释") || chip.includes("分析") ? "improve" : "generate")}
                    className="rounded-md border bg-muted/30 px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted disabled:opacity-50"
                  >
                    {chip}
                  </button>
                ))}
              </div>
            </div>
            <form
              className="border-t p-3"
              onSubmit={(e) => {
                e.preventDefault();
                void runAi(aiPrompt);
                setAiPrompt("");
              }}
            >
              <textarea
                value={aiPrompt}
                onChange={(e) => setAiPrompt(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void runAi(aiPrompt);
                    setAiPrompt("");
                  }
                }}
                rows={2}
                placeholder="输入要生成的策略说明…"
                className="w-full resize-none rounded-md border bg-background px-2 py-2 text-xs outline-none focus:border-primary"
              />
              <button
                type="submit"
                disabled={aiLoading || !aiPrompt.trim()}
                className="mt-1 w-full rounded-md bg-primary py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
              >
                {aiLoading ? "生成中…" : "生成代码"}
              </button>
            </form>
          </aside>
        ) : (
          <button
            type="button"
            onClick={() => setAiOpen(true)}
            className="border-s px-3 py-2 text-xs text-muted-foreground hover:bg-muted"
          >
            <Sparkles className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );
}
