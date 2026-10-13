"use client";

import * as DropdownMenu from "@radix-ui/react-dropdown-menu";

const trigger = "min-h-11 rounded-control border border-control-border px-3 text-sm";
const content = "max-w-sm rounded-panel border border-divider bg-surface p-3 text-sm text-ink shadow-md";

/**
 * Notifications and the help/accessibility menu (global shell). There is no notification source yet:
 * the menu says so instead of showing an empty bell. Radix menus are keyboard and screen-reader
 * accessible; items are plain text, so selecting one does nothing.
 */
export function ShellMenus({ labels }: { labels: { notifications: string; none: string; help: string; items: string[] } }) {
  return (
    <>
      <DropdownMenu.Root>
        <DropdownMenu.Trigger className={trigger}>{labels.notifications}</DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content align="end" sideOffset={4} className={content}>
            <DropdownMenu.Item className="rounded-control p-2 outline-none data-[highlighted]:bg-surface-2">{labels.none}</DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
      <DropdownMenu.Root>
        <DropdownMenu.Trigger className={trigger}>{labels.help}</DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content align="end" sideOffset={4} className={content}>
            {labels.items.map((item) => (
              <DropdownMenu.Item key={item} className="rounded-control p-2 outline-none data-[highlighted]:bg-surface-2">{item}</DropdownMenu.Item>
            ))}
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    </>
  );
}
