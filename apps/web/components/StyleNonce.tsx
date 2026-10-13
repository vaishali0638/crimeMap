"use client";

import { setNonce } from "get-nonce";

/**
 * Radix's scroll lock (react-remove-scroll) injects a <style> element through react-style-singleton,
 * which takes its nonce from get-nonce. Without one, the strict CSP (style-src with a nonce, no
 * 'unsafe-inline') blocks it whenever a dialog or menu opens. Set before any dialog mounts.
 */
export function StyleNonce({ nonce }: { nonce: string }) {
  if (nonce) setNonce(nonce);
  return null;
}
