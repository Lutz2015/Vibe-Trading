import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Network, Plus, Trash2, RotateCcw, Sparkles, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { safeGet, safeSet } from "@/lib/storage";
import { api } from "@/lib/api";

type NodeKind = "trigger" | "logic" | "sector" | "ticker";

type ChainNode = {
  id: string;
  kind: NodeKind;
  title: string;
  body: string;
  x: number;
  y: number;
};

type ChainEdge = {
  id: string;
  from: string;
  to: string;
};

type Chain = {
  id: string;
  name: string;
  nodes: ChainNode[];
  edges: ChainEdge[];
};

const KIND_META: Record<
  NodeKind,
  { label: string; border: string; bg: string; chip: string }
> = {
  trigger: {
    label: "触发因素",
    border: "border-rose-500/80",
    bg: "bg-rose-500/10",
    chip: "bg-rose-500/20 text-rose-300",
  },
  logic: {
    label: "传导逻辑",
    border: "border-amber-400/80",
    bg: "bg-amber-400/10",
    chip: "bg-amber-400/20 text-amber-200",
  },
  sector: {
    label: "受益板块",
    border: "border-violet-500/80",
    bg: "bg-violet-500/10",
    chip: "bg-violet-500/20 text-violet-200",
  },
  ticker: {
    label: "具体标的",
    border: "border-emerald-400/80",
    bg: "bg-emerald-400/10",
    chip: "bg-emerald-400/20 text-emerald-200",
  },
};

const STORAGE_KEY = "qa-logic-chains-v1";

function uid(prefix: string): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 9)}`;
}

function seedChains(): Chain[] {
  const n = (
    id: string,
    kind: NodeKind,
    title: string,
    body: string,
    x: number,
    y: number,
  ): ChainNode => ({ id, kind, title, body, x, y });

  const aiChain: Chain = {
    id: "ai_chip",
    name: "中美AI竞争→西芯东电",
    nodes: [
      n("t1", "trigger", "H20解禁出口", "H20已非最先进→中美算力分工：美出芯、中出电", 80, 120),
      n("t2", "trigger", "算力投入差距收窄", "美AI投资 vs 中国差距从大幅领先收窄", 80, 320),
      n("l1", "logic", "AI竞争转向性价比", "模型参数不再决定胜负→落地/成本/人才成为关键", 340, 200),
      n("l2", "logic", "中国职场AI采用率>80%", "应用侧需求快速放量，带动算力基础设施", 340, 400),
      n("s1", "sector", "中国算力基础设施", "全球算力建设向中国转移，算力税替代石油税", 600, 140),
      n("s2", "sector", "光模块(国产化)", "高速光互连国产化，中际旭创等受益", 600, 340),
      n("k1", "ticker", "中际旭创", "光模块龙头 · 300308", 840, 240),
    ],
    edges: [
      { id: "e1", from: "t1", to: "l1" },
      { id: "e2", from: "t2", to: "l1" },
      { id: "e3", from: "t2", to: "l2" },
      { id: "e4", from: "l1", to: "s1" },
      { id: "e5", from: "l1", to: "s2" },
      { id: "e6", from: "l2", to: "s1" },
      { id: "e7", from: "s2", to: "k1" },
    ],
  };

  const robotChain: Chain = {
    id: "robot",
    name: "人形机器人量产→核心零部件",
    nodes: [
      n("t1", "trigger", "量产元年临近", "多家主机厂公布量产时间表与出货指引", 80, 160),
      n("l1", "logic", "BOM成本下台阶", "关节模批量化，单机成本进入消费级区间", 340, 160),
      n("s1", "sector", "减速器/丝杠", "谐波/RV 减速器与行星滚柱丝杠需求弹性最大", 600, 120),
      n("s2", "sector", "执行器/电机", "空心杯电机、无框力矩电机与驱动器", 600, 320),
      n("k1", "ticker", "绿的谐波", "谐波减速器 · 688017", 840, 100),
      n("k2", "ticker", "拓普集团", "执行器总成 · 601689", 840, 280),
    ],
    edges: [
      { id: "e1", from: "t1", to: "l1" },
      { id: "e2", from: "l1", to: "s1" },
      { id: "e3", from: "l1", to: "s2" },
      { id: "e4", from: "s1", to: "k1" },
      { id: "e5", from: "s2", to: "k2" },
    ],
  };

  return [aiChain, robotChain];
}

function loadChains(): Chain[] {
  try {
    const raw = safeGet(STORAGE_KEY);
    if (!raw) return seedChains();
    const parsed = JSON.parse(raw) as Chain[];
    if (!Array.isArray(parsed) || parsed.length === 0) return seedChains();
    return parsed;
  } catch {
    return seedChains();
  }
}

export function LogicChain() {
  const [chains, setChains] = useState<Chain[]>(loadChains);
  const [activeId, setActiveId] = useState(() => loadChains()[0].id);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [linkFrom, setLinkFrom] = useState<string | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ id: string; ox: number; oy: number } | null>(null);

  const active = useMemo(
    () => chains.find((c) => c.id === activeId) ?? chains[0],
    [chains, activeId],
  );

  useEffect(() => {
    safeSet(STORAGE_KEY, JSON.stringify(chains));
  }, [chains]);

  const updateActive = useCallback(
    (updater: (chain: Chain) => Chain) => {
      setChains((prev) => prev.map((c) => (c.id === activeId ? updater(c) : c)));
    },
    [activeId],
  );

  const addNode = (kind: NodeKind) => {
    const meta = KIND_META[kind];
    const count = active.nodes.filter((n) => n.kind === kind).length + 1;
    const node: ChainNode = {
      id: uid("n"),
      kind,
      title: `${meta.label} ${count}`,
      body: "双击编辑文字",
      x: 120 + (count % 4) * 40,
      y: 80 + (count % 5) * 50,
    };
    updateActive((c) => ({ ...c, nodes: [...c.nodes, node] }));
    setSelectedId(node.id);
  };

  const addChain = () => {
    const chain: Chain = {
      id: uid("c"),
      name: `新建逻辑链 ${chains.length + 1}`,
      nodes: [],
      edges: [],
    };
    setChains((prev) => [...prev, chain]);
    setActiveId(chain.id);
  };

  const deleteSelected = () => {
    if (!selectedId) return;
    updateActive((c) => ({
      ...c,
      nodes: c.nodes.filter((n) => n.id !== selectedId),
      edges: c.edges.filter((e) => e.from !== selectedId && e.to !== selectedId),
    }));
    setSelectedId(null);
    setLinkFrom(null);
  };

  const resetDemo = () => {
    const seeded = seedChains();
    setChains(seeded);
    setActiveId(seeded[0].id);
    setSelectedId(null);
  };

  const onPointerDownNode = (event: React.PointerEvent, node: ChainNode) => {
    event.stopPropagation();
    setSelectedId(node.id);
    if (event.shiftKey || linkFrom) {
      if (linkFrom && linkFrom !== node.id) {
        updateActive((c) => ({
          ...c,
          edges: [
            ...c.edges.filter((e) => !(e.from === linkFrom && e.to === node.id)),
            { id: uid("e"), from: linkFrom, to: node.id },
          ],
        }));
        setLinkFrom(null);
        return;
      }
      setLinkFrom(node.id);
      return;
    }
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    dragRef.current = {
      id: node.id,
      ox: event.clientX - rect.left - node.x,
      oy: event.clientY - rect.top - node.y,
    };
    (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
  };

  const onPointerMove = (event: React.PointerEvent) => {
    const drag = dragRef.current;
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!drag || !rect) return;
    const x = Math.max(8, Math.min(rect.width - 180, event.clientX - rect.left - drag.ox));
    const y = Math.max(8, Math.min(rect.height - 100, event.clientY - rect.top - drag.oy));
    updateActive((c) => ({
      ...c,
      nodes: c.nodes.map((n) => (n.id === drag.id ? { ...n, x, y } : n)),
    }));
  };

  const onPointerUp = () => {
    dragRef.current = null;
  };

  const [editNodeState, setEditNodeState] = useState<ChainNode | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editBody, setEditBody] = useState("");

  const editNode = (node: ChainNode) => {
    setEditNodeState(node);
    setEditTitle(node.title);
    setEditBody(node.body);
  };

  const saveEditNode = () => {
    if (!editNodeState) return;
    updateActive((c) => ({
      ...c,
      nodes: c.nodes.map((n) =>
        n.id === editNodeState.id
          ? {
              ...n,
              title: editTitle.trim() || n.title,
              body: editBody,
            }
          : n,
      ),
    }));
    setEditNodeState(null);
  };

  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiModalOpen, setAiModalOpen] = useState(false);
  const [aiTopic, setAiTopic] = useState("");

  const openAiModal = () => {
    setAiTopic(active.name || "今日热点");
    setAiError(null);
    setAiModalOpen(true);
  };

  const generateWithAi = async (topicInput?: string) => {
    const topic = (topicInput ?? aiTopic).trim();
    if (!topic) return;
    setAiModalOpen(false);
    setAiLoading(true);
    setAiError(null);
    try {
      const res = await api.analyzeInsight({
        kind: "logic_chain",
        locale: navigator.language || "zh-CN",
        payload: {
          mode: "generate",
          topic: topic.trim(),
          // Omit context → backend injects live hot boards/stocks/headlines.
          nodes: active.nodes.map((n) => ({
            kind: n.kind,
            title: n.title,
            body: n.body,
          })),
        },
      });
      if (!res.ok || !res.text) {
        setAiError(res.error || "AI 生成失败");
        return;
      }
      const parsed = res.text
        .split("\n")
        .map((line) => line.trim().replace(/^[-•]\s*/, ""))
        .filter((line) => line.includes("|"))
        .map((line) => {
          const parts = line.split("|").map((p) => p.trim());
          const kind = (parts[0] || "").toLowerCase() as NodeKind;
          if (!KIND_META[kind] || !parts[1]) return null;
          return { kind, title: parts[1], body: parts[2] || "" };
        })
        .filter(Boolean) as { kind: NodeKind; title: string; body: string }[];
      if (parsed.length === 0) {
        setAiError("未能从 AI 回复中解析节点，请重试或换个主题");
        return;
      }
      const nodes: ChainNode[] = parsed.map((p, i) => ({
        id: uid("n"),
        kind: p.kind,
        title: p.title,
        body: p.body,
        x: 80 + (i % 4) * 220,
        y: 80 + Math.floor(i / 4) * 160,
      }));
      const edges: ChainEdge[] = nodes.slice(0, -1).map((n, i) => ({
        id: uid("e"),
        from: n.id,
        to: nodes[i + 1].id,
      }));
      const chainId = uid("c");
      const chain: Chain = {
        id: chainId,
        name: topic.trim(),
        nodes,
        edges,
      };
      setChains((prev) => [...prev, chain]);
      setActiveId(chainId);
    } catch (e) {
      setAiError(e instanceof Error ? e.message : "AI 生成失败");
    } finally {
      setAiLoading(false);
    }
  };

  const nodeCenter = (id: string) => {
    const n = active.nodes.find((x) => x.id === id);
    return n ? { x: n.x + 90, y: n.y + 40 } : { x: 0, y: 0 };
  };

  return (
    <div className="mx-auto flex h-full max-w-6xl flex-col gap-3 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Network className="h-6 w-6 text-primary" />
            <h1 className="text-xl font-bold">逻辑链图谱</h1>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            触发因素 → 传导逻辑 → 受益板块 → 具体标的 · Shift+点击首节点再点目标可连线 · 双击编辑
          </p>
        </div>
        <button
          type="button"
          onClick={openAiModal}
          disabled={aiLoading}
          className="inline-flex items-center gap-1.5 rounded-md border border-primary/40 bg-primary/10 px-2.5 py-1.5 text-xs text-primary hover:bg-primary/20 disabled:opacity-60"
        >
          {aiLoading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Sparkles className="h-3.5 w-3.5" />
          )}
          AI 生成逻辑链（含实时热点）
        </button>
        <button
          type="button"
          onClick={resetDemo}
          className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-muted"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          恢复示例
        </button>
      </div>

      {aiLoading ? (
        <div className="flex items-center gap-2 rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-xs text-primary">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          AI 正在生成逻辑链（约 15–60 秒，依赖 LLM 与热点缓存）…
        </div>
      ) : null}

      {aiError ? (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-600 dark:text-amber-300">
          {aiError}
        </div>
      ) : null}

      {editNodeState ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="logic-chain-edit-title"
        >
          <div className="w-full max-w-md rounded-xl border bg-card p-4 shadow-lg">
            <h2 id="logic-chain-edit-title" className="text-sm font-semibold">
              编辑节点
            </h2>
            <label className="mt-3 block text-xs text-muted-foreground">标题</label>
            <input
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              autoFocus
              className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
            />
            <label className="mt-3 block text-xs text-muted-foreground">描述</label>
            <textarea
              value={editBody}
              onChange={(e) => setEditBody(e.target.value)}
              rows={3}
              className="mt-1 w-full resize-y rounded-md border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
            />
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setEditNodeState(null)}
                className="rounded-md border px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted"
              >
                取消
              </button>
              <button
                type="button"
                onClick={saveEditNode}
                className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground"
              >
                保存
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {aiModalOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="logic-chain-ai-title"
        >
          <div className="w-full max-w-md rounded-xl border bg-card p-4 shadow-lg">
            <h2 id="logic-chain-ai-title" className="text-sm font-semibold">
              AI 生成逻辑链
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              输入主题，AI 将结合缓存中的热点板块/个股与快讯生成节点。
            </p>
            <input
              value={aiTopic}
              onChange={(e) => setAiTopic(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void generateWithAi();
              }}
              autoFocus
              placeholder="例如：算力国产化、人形机器人量产"
              className="mt-3 w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
            />
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setAiModalOpen(false)}
                className="rounded-md border px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void generateWithAi()}
                disabled={!aiTopic.trim()}
                className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
              >
                开始生成
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <div className="flex flex-wrap gap-2">
        {chains.map((c) => (
          <button
            key={c.id}
            type="button"
            onClick={() => {
              setActiveId(c.id);
              setSelectedId(null);
              setLinkFrom(null);
            }}
            className={cn(
              "rounded-md px-3 py-1.5 text-xs transition",
              c.id === activeId
                ? "bg-primary text-primary-foreground"
                : "border bg-card text-foreground hover:bg-muted",
            )}
          >
            {c.name}
          </button>
        ))}
        <button
          type="button"
          onClick={addChain}
          className="rounded-md border border-dashed px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted"
        >
          <Plus className="mr-1 inline h-3 w-3" />
          新建链
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">添加节点</span>
        {(Object.keys(KIND_META) as NodeKind[]).map((kind) => (
          <button
            key={kind}
            type="button"
            onClick={() => addNode(kind)}
            className={cn(
              "rounded-md border px-2.5 py-1 text-xs",
              KIND_META[kind].border,
              KIND_META[kind].bg,
            )}
          >
            <Plus className="mr-1 inline h-3 w-3" />
            {KIND_META[kind].label}
          </button>
        ))}
        <button
          type="button"
          onClick={deleteSelected}
          disabled={!selectedId}
          className="ml-2 inline-flex items-center gap-1 rounded-md border px-2.5 py-1 text-xs text-muted-foreground disabled:opacity-40"
        >
          <Trash2 className="h-3 w-3" />
          删除选中
        </button>
        {linkFrom ? (
          <span className="text-[11px] text-primary">连线中：再点击目标节点…</span>
        ) : null}
      </div>

      <div
        ref={canvasRef}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerUp}
        onClick={() => {
          setSelectedId(null);
          setLinkFrom(null);
        }}
        className="relative min-h-[480px] flex-1 overflow-auto rounded-xl border bg-card"
        style={{
          backgroundImage:
            "radial-gradient(circle at 1px 1px, hsl(var(--border) / 0.45) 1px, transparent 0)",
          backgroundSize: "18px 18px",
        }}
      >
        <svg className="pointer-events-none absolute inset-0 h-full w-full">
          {active.edges.map((edge) => {
            const a = nodeCenter(edge.from);
            const b = nodeCenter(edge.to);
            return (
              <line
                key={edge.id}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke="currentColor"
                className="text-muted-foreground/60"
                strokeWidth="1.5"
                strokeDasharray="4 4"
              />
            );
          })}
        </svg>

        {active.nodes.map((node) => {
          const meta = KIND_META[node.kind];
          const selected = selectedId === node.id;
          return (
            <div
              key={node.id}
              role="button"
              tabIndex={0}
              onPointerDown={(e) => onPointerDownNode(e, node)}
              onDoubleClick={() => editNode(node)}
              onKeyDown={(e) => {
                if (e.key === "Delete" || e.key === "Backspace") {
                  setSelectedId(node.id);
                }
              }}
              className={cn(
                "absolute w-[180px] cursor-grab rounded-lg border-2 px-3 py-2 shadow-lg backdrop-blur select-none active:cursor-grabbing",
                meta.border,
                meta.bg,
                selected && "ring-2 ring-primary ring-offset-2 ring-offset-background",
                linkFrom === node.id && "ring-2 ring-sky-400",
              )}
              style={{ left: node.x, top: node.y }}
            >
              <div
                className={cn(
                  "mb-1 inline-flex rounded px-1.5 py-0.5 text-[10px]",
                  meta.chip,
                )}
              >
                {meta.label}
              </div>
              <div className="text-xs font-semibold leading-snug">{node.title}</div>
              <div className="mt-1 text-[11px] leading-snug text-muted-foreground">
                {node.body}
              </div>
            </div>
          );
        })}

        {active.nodes.length === 0 ? (
          <div className="absolute inset-0 grid place-items-center text-sm text-muted-foreground">
            点击上方按钮添加「触发因素 / 传导逻辑 / 受益板块 / 具体标的」
          </div>
        ) : null}
      </div>
    </div>
  );
}
