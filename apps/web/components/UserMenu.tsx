"use client";

import * as DropdownMenu from "@radix-ui/react-dropdown-menu";

type Props = {
  name: string;
  roles: { role: string; regions: string[] }[];
  mfa: boolean;
  csrfToken: string;
  labels: { menu: string; roles: string; noRoles: string; mfa: string; signOut: string };
};

/**
 * Account menu (Radix: keyboard and screen-reader accessible). Sign-out is a CSRF-protected POST
 * made by a real submit button. `onSelect` keeps the menu open so Radix does not unmount the form
 * before the browser submits it (found in Phase 1 browser testing); the page then navigates away.
 */
export function UserMenu({ name, roles, mfa, csrfToken, labels }: Props) {
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        aria-label={`${labels.menu}: ${name}`}
        className="min-h-11 rounded-control border border-control-border px-3 text-sm"
      >
        {name}
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={4}
          className="min-w-72 rounded-panel border border-divider bg-surface p-3 text-sm text-ink shadow-md"
        >
          <DropdownMenu.Label className="font-semibold">{labels.roles}</DropdownMenu.Label>
          {roles.length === 0 ? (
            <p className="py-1">{labels.noRoles}</p>
          ) : (
            <ul className="py-1">
              {roles.map((r) => (
                <li key={r.role}>
                  {r.role}
                  {r.regions.length > 0 ? ` — ${r.regions.join(", ")}` : ""}
                </li>
              ))}
            </ul>
          )}
          <p className="py-1">
            {labels.mfa}: {mfa ? "✓ verified" : "✗ not verified"}
          </p>
          <DropdownMenu.Separator className="my-2 h-px bg-divider" />
          <DropdownMenu.Item asChild>
            <a
              href="/login"
              className="flex min-h-11 w-full items-center rounded-control px-2 text-left font-semibold text-navy data-[highlighted]:bg-surface-2"
            >
              Switch Role (1-Click Sign In)
            </a>
          </DropdownMenu.Item>
          <form method="post" action="/bff/auth/logout">
            <input type="hidden" name="csrf" value={csrfToken} />
            <DropdownMenu.Item asChild onSelect={(event) => event.preventDefault()}>
              <button
                type="submit"
                className="flex min-h-11 w-full items-center rounded-control px-2 text-left font-semibold text-teal-text data-[highlighted]:bg-surface-2"
              >
                {labels.signOut}
              </button>
            </DropdownMenu.Item>
          </form>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
