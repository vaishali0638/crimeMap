import type { Metadata } from "next";
import { headers } from "next/headers";
import type { ReactNode } from "react";
import { StyleNonce } from "@/components/StyleNonce";
import { environmentLabel } from "@/lib/environment";
import "./globals.css";

export function generateMetadata(): Metadata {
  const label = environmentLabel();
  return {
    title: `CRIMEMAP — ${label}`,
    description: `Internal police decision-support (${label}).`,
    robots: { index: false, follow: false },
  };
}

export default async function RootLayout({ children }: { children: ReactNode }) {
  const nonce = (await headers()).get("x-nonce") ?? "";
  return (
    <html lang="en">
      <body>
        <StyleNonce nonce={nonce} />
        {children}
      </body>
    </html>
  );
}
