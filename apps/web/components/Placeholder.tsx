import { t } from "@/lib/i18n";

/** Honest placeholder for screens not built yet — no fake data. */
export function Placeholder({ title, phase }: { title: string; phase: number }) {
  return (
    <section aria-labelledby="page-title" className="max-w-3xl">
      <h1 id="page-title" className="text-2xl font-semibold text-navy">{title}</h1>
      <p className="mt-2">{t("placeholder.phase", { phase })}</p>
    </section>
  );
}
