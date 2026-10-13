import { environmentLabel } from "@/lib/environment";
import Link from "next/link";
import { t } from "@/lib/i18n";

export const dynamic = "force-dynamic";

export default function NotFound() {
  return (
    <main id="main" className="mx-auto max-w-xl px-6 py-16">
      <p className="font-semibold">
        {environmentLabel()} · {t("safeguard.classification")}
      </p>
      <h1 className="mt-4 text-2xl font-semibold text-navy">{t("notFound.title")}</h1>
      <p className="mt-2">{t("notFound.body")}</p>
      <p className="mt-6">
        <Link href="/" className="font-semibold">
          {t("error.home")}
        </Link>
      </p>
    </main>
  );
}
