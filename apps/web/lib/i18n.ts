import { en, type MessageKey } from "@/messages/en";

const catalogues = { en } as const;
export type Locale = keyof typeof catalogues;

export function t(key: MessageKey, vars: Record<string, string | number> = {}, locale: Locale = "en"): string {
  // A key missing from the catalogue (e.g. a new API value) shows the key, never breaks the page.
  const template: string = (catalogues[locale] as Record<string, string | undefined>)[key] ?? key;
  return template.replace(/\{(\w+)\}/g, (_, name: string) => String(vars[name] ?? `{${name}}`));
}

type PluralBase = { [K in MessageKey]: K extends `${infer B}.one` ? B : never }[MessageKey];

/** Count-dependent message: uses `<base>.one` / `<base>.other` by the locale's plural rules. */
export function tn(base: PluralBase, count: number, vars: Record<string, string | number> = {}, locale: Locale = "en"): string {
  const form = new Intl.PluralRules(locale).select(count) === "one" ? "one" : "other";
  return t(`${base}.${form}` as MessageKey, { count, ...vars }, locale);
}
