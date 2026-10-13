import { environmentLabel } from "@/lib/environment";
import { t } from "@/lib/i18n";

// Dynamic so the per-request CSP nonce is applied (nonces require dynamic rendering).
export const dynamic = "force-dynamic";

export default function SignedOutPage() {
  return (
    <main id="main" className="mx-auto max-w-xl px-6 py-16">
      <p className="font-semibold">{environmentLabel()} · {t("safeguard.classification")}</p>
      <h1 className="mt-4 text-2xl font-semibold text-navy">{t("signedOut.title")}</h1>
      <p className="mt-2">{t("signedOut.body")}</p>
      <div className="mt-6 flex flex-wrap items-center gap-4">
        <form method="POST" action="/bff/auth/demo-login">
          <input type="hidden" name="username" value="demo.analyst" />
          <input type="hidden" name="returnTo" value="/" />
          <button
            type="submit"
            className="min-h-11 rounded-control bg-navy px-5 py-2.5 text-sm font-semibold text-on-navy"
          >
            ⚡ 1-Click Sign In (Crime Analyst)
          </button>
        </form>
        <a
          href="/login"
          className="inline-flex min-h-11 items-center rounded-control border border-control-border px-4 text-sm font-semibold text-navy"
        >
          {t("signedOut.again")}
        </a>
      </div>
    </main>
  );
}
