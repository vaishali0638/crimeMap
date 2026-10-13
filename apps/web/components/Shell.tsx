import Link from "next/link";
import type { ReactNode } from "react";
import type { Freshness, Me } from "@/lib/api";
import { t } from "@/lib/i18n";
import type { MessageKey } from "@/messages/en";
import { SafeguardStrip } from "./SafeguardStrip";
import { ShellMenus } from "./ShellMenus";
import { UserMenu } from "./UserMenu";

// `permission` only hides links the user cannot use; each page and the API still check access.
const NAV: { href: string; key: MessageKey; permission?: string }[] = [
  { href: "/", key: "nav.home" },
  { href: "/dashboard", key: "nav.dashboard" },
  { href: "/analytics", key: "nav.analytics" },
  { href: "/review", key: "nav.review" },
  { href: "/reports", key: "nav.reports" },
  { href: "/ai", key: "nav.ai", permission: "ai.model.read" },
  { href: "/data-health", key: "nav.dataHealth", permission: "lineage.read" },
];

export type QueueBadge = { count: number; more: boolean } | null;

function badgeText(queue: NonNullable<QueueBadge>): string {
  return queue.count > 999 ? "999+" : queue.more ? `${queue.count}+` : String(queue.count);
}

export function Shell({
  me,
  csrfToken,
  freshness,
  queue = null,
  children,
}: {
  me: Me;
  csrfToken: string;
  freshness: Freshness | "unavailable" | null;
  /** Items waiting in the caller's review queues (null: none they work, or unavailable). */
  queue?: QueueBadge;
  children: ReactNode;
}) {
  const nav = NAV.filter((item) => !item.permission || me.permissions.includes(item.permission));
  return (
    <>
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:bg-surface focus:p-2">
        {t("skip")}
      </a>
      <header>
        <SafeguardStrip environment={me.environment} freshness={freshness} />
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-divider bg-surface px-6 py-2">
          <span className="text-lg font-semibold tracking-wide text-navy">{t("app.name")}</span>
          <nav aria-label={t("nav.label")}>
            <ul className="flex flex-wrap gap-1">
              {nav.map((item) => (
                <li key={item.href}>
                  <Link href={item.href} className="inline-flex min-h-11 items-center gap-2 whitespace-nowrap rounded-control px-3 text-sm font-medium text-ink hover:bg-surface-2">
                    {t(item.key)}
                    {item.href === "/review" && queue && queue.count > 0 ? (
                      <span className="rounded-full bg-navy px-2 text-xs font-semibold text-on-navy">
                        <span aria-hidden>{badgeText(queue)}</span>
                        <span className="sr-only">{t("nav.reviewBadge", { count: badgeText(queue) })}</span>
                      </span>
                    ) : null}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <form role="search" method="get" action="/">
              <input
                type="search"
                name="q"
                minLength={2}
                maxLength={100}
                aria-label={t("nav.search")}
                placeholder={t("nav.search")}
                className="min-h-11 w-full min-w-0 rounded-control border border-control-border bg-surface px-3 text-sm sm:w-48 lg:w-72"
              />
            </form>
            <ShellMenus
              labels={{
                notifications: t("nav.notifications"),
                none: t("nav.noNotifications"),
                help: t("nav.help"),
                items: [t("help.keyboard"), t("help.data"), t("help.motion"), t("help.privacy")],
              }}
            />
            <UserMenu
              name={me.name ?? me.username ?? t("user.fallbackName")}
              roles={me.roles}
              mfa={me.mfa}
              csrfToken={csrfToken}
              labels={{
                menu: t("user.menu"),
                roles: t("user.roles"),
                noRoles: t("user.noRoles"),
                mfa: t("user.mfa"),
                signOut: t("user.signOut"),
              }}
            />
          </div>
        </div>
      </header>
      <main id="main" className="px-6 py-8">
        {children}
      </main>
    </>
  );
}
