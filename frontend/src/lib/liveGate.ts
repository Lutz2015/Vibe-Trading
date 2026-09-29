import type { TFunction } from "i18next";
import type { QbitAutomationStatus } from "@/lib/api";

/** True when automation is in live mode but mandate/halt/env gate is not ready. */
export function isLiveGateBlocked(automation: QbitAutomationStatus | null | undefined): boolean {
  if (!automation || automation.execution_mode !== "live") return false;
  const gate = automation.live_gate;
  if (!gate) return true;
  return !gate.ready;
}

export function formatLiveGateReasons(
  reasons: string[] | undefined,
  t: TFunction,
): string {
  if (!reasons?.length) {
    return t("liveGate.blockedGeneric", { defaultValue: "Live gate not ready" });
  }
  return reasons
    .map((code) =>
      t(`liveGate.reason.${code}`, {
        defaultValue: code,
      }),
    )
    .join(" · ");
}
