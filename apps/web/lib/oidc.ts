import "server-only";
import * as client from "openid-client";
import { serverEnv } from "./env";

let configPromise: Promise<client.Configuration> | undefined;

/** OIDC client configuration from the provider's discovery document (cached per process). */
export function oidcConfig(): Promise<client.Configuration> {
  if (!configPromise) {
    const env = serverEnv();
    const insecure = env.CRIMEMAP_OIDC_ISSUER.startsWith("http://");
    if (insecure && env.CRIMEMAP_ENVIRONMENT === "PRODUCTION") {
      throw new Error("refusing an http identity provider in PRODUCTION");
    }
    configPromise = client
      .discovery(
        new URL(env.CRIMEMAP_OIDC_ISSUER),
        env.CRIMEMAP_OIDC_CLIENT_ID,
        env.CRIMEMAP_KC_WEB_CLIENT_SECRET,
        undefined,
        // DEMO/SANDBOX only: the local Keycloak serves plain http.
        insecure ? { execute: [client.allowInsecureRequests] } : undefined,
      )
      .catch((error: unknown) => {
        configPromise = undefined; // retry discovery on the next request
        throw error;
      });
  }
  return configPromise;
}

export function callbackUrl(): string {
  return new URL("/bff/auth/callback", serverEnv().CRIMEMAP_WEB_BASE_URL).toString();
}

export { client };
