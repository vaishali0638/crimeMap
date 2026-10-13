"use client";

import { t } from "@/lib/i18n";

/**
 * Last-resort error page (the root layout itself failed). It cannot read server configuration, so
 * it states the classification and that any data in this application may be synthetic.
 */
export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en">
      <body>
        <main id="main" className="mx-auto max-w-xl px-6 py-16">
          <p className="font-semibold">CRIMEMAP · {t("safeguard.restricted")}</p>
          <p className="text-sm">{t("safeguard.globalNotice")}</p>
          <h1 className="mt-4 text-2xl font-semibold">{t("error.title")}</h1>
          <p className="mt-2">{t("error.body")}</p>
          <button type="button" onClick={reset} className="mt-6 min-h-11 px-4 font-semibold">
            {t("error.retry")}
          </button>
        </main>
      </body>
    </html>
  );
}
