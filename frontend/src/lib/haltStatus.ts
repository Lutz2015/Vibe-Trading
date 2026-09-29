import type { TFunction } from "i18next";
import type { QbitHaltDetail, QbitOpsStatus } from "@/lib/api";

export function isHaltActive(ops: QbitOpsStatus | null | undefined): boolean {
  return Boolean(ops?.killSwitch || ops?.halt?.active);
}

export function formatHaltDetail(
  halt: QbitHaltDetail | null | undefined,
  t: TFunction,
): string | undefined {
  if (!halt?.active) return undefined;

  const scope =
    halt.scope === "broker" && halt.broker
      ? t("tradingCenter.haltScopeBrokerNamed", {
          defaultValue: `Broker (${halt.broker})`,
          broker: halt.broker,
        })
      : t(`tradingCenter.haltScope.${halt.scope}`, { defaultValue: halt.scope });

  const source = t(`tradingCenter.haltSource.${halt.source}`, {
    defaultValue: halt.source,
  });

  return t("tradingCenter.haltDetailLine", {
    defaultValue: `${scope} · ${source}`,
    scope,
    source,
  });
}
