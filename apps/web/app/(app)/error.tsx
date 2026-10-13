"use client";

import Link from "next/link";
import { t } from "@/lib/i18n";

/**
 * Error boundary for app pages. It renders inside the app layout, so the safeguard strip
 * (environment, DEMO / SYNTHETIC DATA, classification) stays visible. No error detail is shown.
 */
export default function AppError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <section aria-labelledby="error-title" role="alert" className="max-w-3xl">
      <h1 id="error-title" className="text-2xl font-semibold text-navy">
        {t("error.title")}
      </h1>
      <p className="mt-2">{t("error.body")}</p>
      <div className="mt-4 flex gap-4">
        <button type="button" onClick={reset} className="min-h-11 rounded-control bg-navy px-4 text-sm font-semibold text-on-navy">
          {t("error.retry")}
        </button>
        <Link href="/" className="inline-flex min-h-11 items-center font-semibold">
          {t("error.home")}
        </Link>
      </div>
    </section>
  );
}
