import Link from "next/link";
import { Flash, SwitchForm } from "@/components/workflow/Forms";
import { Facts, Notice } from "@/components/workflow/View";
import { type ModelList, entries } from "@/lib/ai";
import { apiFetch } from "@/lib/api";
import { requireUser } from "@/lib/current-user";
import { formatIst } from "@/lib/dq";
import { t } from "@/lib/i18n";
import { flashKey, flashNonce } from "@/lib/workflow";
import type { MessageKey } from "@/messages/en";

export const dynamic = "force-dynamic";

/** Model registry: model cards, approval, rollout gates and kill switches (ADR-037). */
export default async function ModelsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const query = await searchParams;
  const done = flashKey(query.done);
  const nonce = flashNonce(query.n);
  const { me, session } = await requireUser();
  if (!me.permissions.includes("ai.model.read")) return <Notice title={t("ai.models.title")} text={t("ai.models.noAccess")} />;
  let response: Response;
  try {
    response = await apiFetch(session, "/api/v1/ai/models");
  } catch {
    return <Notice title={t("ai.models.title")} text={t("wf.unavailable")} />;
  }
  if (!response.ok) return <Notice title={t("ai.models.title")} text={t("wf.unavailable")} />;
  const list = (await response.json()) as ModelList;
  const admin = me.permissions.includes("ai.config");
  return (
    <div className="flex max-w-5xl flex-col gap-6">
      {done ? <Flash key={nonce} text={t(`wf.flash.${done}`)} /> : null}
      <header>
        <h1 id="page-title" className="text-2xl font-semibold text-navy">{t("ai.models.title")}</h1>
        <p className="mt-1 text-sm">{t("ai.models.intro")}</p>
        {me.permissions.includes("ai.insight.read") ? (
          <p className="mt-2"><Link href="/review?tab=ai">{t("ai.models.toQueue")}</Link> · <Link href="/ai/briefing">{t("ai.briefing.title")}</Link></p>
        ) : null}
      </header>
      <section aria-labelledby="global-title" className="flex flex-col gap-2 rounded-panel border border-divider bg-surface-2 p-4">
        <h2 id="global-title" className="text-xl font-semibold text-navy">{t("ai.models.switches")}</h2>
        <Facts
          rows={(["ai_assistance", "ai_copilot", "ai_forecasting"] as const).map((k) => [t(`ai.flag.${k}`), list[k] ? t("ai.on") : t("ai.off")])}
        />
        {admin ? (
          <SwitchForm key={`flag-${list.ai_assistance}`} path="ai/settings" body={{ flag: "ai_assistance" }} enabled={list.ai_assistance} label={t("ai.flag.ai_assistance")} csrfToken={session.csrfToken} />
        ) : null}
      </section>
      {list.data.map((m) => (
        <section key={m.id} aria-labelledby={`m-${m.id}`} className="flex flex-col gap-2 rounded-panel border border-divider p-4">
          <h2 id={`m-${m.id}`} className="text-lg font-semibold text-navy">
            {m.name} <span className="font-mono text-sm">{m.code}@{m.version}</span>
          </h2>
          <p className="font-semibold">
            {t(`ai.approval.${m.approval_status}` as MessageKey)} · {m.is_enabled ? t("ai.on") : t("ai.off")}
            {m.gates_met === false ? ` · ${t("ai.models.gatesUnmet")}` : ""}
          </p>
          <p className="text-sm">{m.description}</p>
          <Facts
            rows={[
              [t("ai.models.kind"), t(`ai.kind.${m.kind}` as MessageKey)],
              [t("ai.models.intended"), m.intended_use],
              [t("ai.models.prohibited"), m.prohibited_uses.join("\n")],
              [t("ai.models.features"), m.features.join(", ")],
              [t("ai.run.parameters"), entries(m.parameters).map(([k, v]) => `${k}: ${v}`).join("\n") || "—"],
              [t("ai.models.training"), m.training_data_period ?? "—"],
              [t("ai.models.evaluation"), entries(m.evaluation_metrics).map(([k, v]) => `${k}: ${v}`).join("\n") || "—"],
              [t("ai.models.bias"), m.bias_fairness_notes],
              [t("ai.card.limitations"), m.limitations.join("\n")],
              [t("ai.models.owner"), m.owner],
              [t("ai.models.approval"), m.approval_note ?? "—"],
              [t("ai.models.gates"), Object.keys(m.rollout_gates).length ? entries(m.rollout_gates).map(([k, v]) => `${k}: ${v}`).join("\n") : t("ai.models.noGates")],
              [t("ai.models.rollback"), m.rollback_to ?? t("ai.models.noRollback")],
              [t("ai.models.changed"), m.changed_at ? `${m.changed_by ?? "—"} · ${formatIst(m.changed_at)} · ${m.change_reason ?? ""}` : "—"],
            ]}
          />
          {admin && m.is_enabled ? (
            <SwitchForm key={`${m.id}-on`} path={`ai/models/${encodeURIComponent(m.id)}/switch`} body={{}} enabled={m.is_enabled} label={m.name} csrfToken={session.csrfToken} />
          ) : null}
          {admin && !m.is_enabled && m.approval_status === "APPROVED" && m.gates_met !== false ? (
            <SwitchForm key={`${m.id}-off`} path={`ai/models/${encodeURIComponent(m.id)}/switch`} body={{}} enabled={false} label={m.name} csrfToken={session.csrfToken} />
          ) : null}
          {admin && !m.is_enabled && m.approval_status !== "APPROVED" ? <p className="text-sm">{t("ai.models.cannotEnable")}</p> : null}
          {admin && !m.is_enabled && m.approval_status === "APPROVED" && m.gates_met === false ? <p className="text-sm">{t("ai.models.cannotEnableGates")}</p> : null}
        </section>
      ))}
    </div>
  );
}
