import "server-only";
import { ENV_LABEL } from "@/components/SafeguardStrip";
import { t } from "@/lib/i18n";

/** The deployment's environment label (from server configuration, never from the browser). */
export function environmentLabel(): string {
  const env = (process.env.CRIMEMAP_ENVIRONMENT ?? "DEMO").toUpperCase();
  return t(ENV_LABEL[env in ENV_LABEL ? (env as keyof typeof ENV_LABEL) : "DEMO"]);
}
