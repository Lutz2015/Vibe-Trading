import { useRef, useState } from "react";
import { FileUp, Loader2, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";

interface Props {
  open: boolean;
  onClose: () => void;
  onImported: (message: string) => void;
}

const EXAMPLE = `# person-trading-strategy.yaml v0.1
meta:
  id: my_momentum
  name: 我的动量策略
  market: a_share
universe:
  symbols:
    - "000001.SZ"
    - "600519.SH"
signals:
  type: builtin
  mode: momentum
  params:
    top_n: 2
execution:
  mode: paper
  time_local: "14:50"
  poll_interval_sec: 60
portfolio:
  initial_cash: 1000000
`;

export function StrategyYamlImportDialog({ open, onClose, onImported }: Props) {
  const { t } = useTranslation();
  const fileRef = useRef<HTMLInputElement>(null);
  const [yaml, setYaml] = useState("");
  const [activate, setActivate] = useState(true);
  const [mergeGlobals, setMergeGlobals] = useState(true);
  const [moduleCode, setModuleCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const needsModuleCode =
    /type:\s*python_module/m.test(yaml) && !/path:\s*\S+/m.test(yaml);
  const liveModeInYaml =
    /execution\s*:[\s\S]*?mode\s*:\s*live/m.test(yaml) ||
    /execution_mode\s*:\s*live/m.test(yaml);

  if (!open) return null;

  const handleFile = async (file: File | null) => {
    if (!file) return;
    const text = await file.text();
    setYaml(text);
    setError(null);
  };

  const submit = async () => {
    if (!yaml.trim()) {
      setError(t("strategyYaml.empty"));
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await api.importStrategyYaml({
        yaml: yaml.trim(),
        activate,
        merge_globals: mergeGlobals,
        module_code: moduleCode.trim() || undefined,
      });
      onImported(res.message || t("strategyYaml.success"));
      setYaml("");
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("strategyYaml.failed"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-xl border bg-background shadow-lg">
        <div className="flex items-center justify-between border-b px-4 py-3">
          <h2 className="text-lg font-semibold">{t("strategyYaml.title")}</h2>
          <button type="button" onClick={onClose} className="rounded-md p-1 hover:bg-muted">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
          <p className="text-sm text-muted-foreground">{t("strategyYaml.desc")}</p>

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              className="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm"
            >
              <FileUp className="h-4 w-4" />
              {t("strategyYaml.pickFile")}
            </button>
            <button
              type="button"
              onClick={() => setYaml(EXAMPLE)}
              className="rounded-md border px-3 py-1.5 text-sm text-muted-foreground"
            >
              {t("strategyYaml.loadExample")}
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".yaml,.yml,text/yaml"
              className="hidden"
              onChange={(e) => void handleFile(e.target.files?.[0] ?? null)}
            />
          </div>

          <textarea
            value={yaml}
            onChange={(e) => setYaml(e.target.value)}
            rows={16}
            spellCheck={false}
            className="min-h-[240px] w-full rounded-md border bg-muted/20 p-3 font-mono text-xs leading-relaxed"
            placeholder={t("strategyYaml.placeholder")}
          />

          {liveModeInYaml ? (
            <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-800 dark:text-amber-200">
              {t("strategyYaml.liveModeWarning")}
            </div>
          ) : null}

          {needsModuleCode ? (
            <div className="grid gap-2">
              <span className="text-sm font-medium">{t("strategyYaml.moduleCode")}</span>
              <textarea
                value={moduleCode}
                onChange={(e) => setModuleCode(e.target.value)}
                rows={8}
                spellCheck={false}
                className="w-full rounded-md border bg-muted/20 p-3 font-mono text-xs"
                placeholder={t("strategyYaml.moduleCodePlaceholder")}
              />
            </div>
          ) : null}

          <div className="flex flex-wrap gap-4 text-sm">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={activate}
                onChange={(e) => setActivate(e.target.checked)}
                className="accent-primary"
              />
              {t("strategyYaml.activate")}
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={mergeGlobals}
                onChange={(e) => setMergeGlobals(e.target.checked)}
                className="accent-primary"
              />
              {t("strategyYaml.mergeGlobals")}
            </label>
          </div>

          {error ? <p className="text-sm text-destructive">{error}</p> : null}
        </div>

        <div className="flex justify-end gap-2 border-t px-4 py-3">
          <button type="button" onClick={onClose} className="rounded-md border px-4 py-2 text-sm">
            {t("strategyYaml.cancel")}
          </button>
          <button
            type="button"
            disabled={loading}
            onClick={() => void submit()}
            className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-50"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {t("strategyYaml.import")}
          </button>
        </div>
      </div>
    </div>
  );
}
