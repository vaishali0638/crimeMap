/**
 * Post-login return path. Only a same-origin, absolute path is accepted; anything that could be
 * interpreted as another origin (//host, /\host, schemes, encoded slashes) or that would loop back
 * into the sign-in flow falls back to "/".
 */
const MAX_LENGTH = 512;

export function safeReturnTo(value: string | null | undefined): string {
  if (!value || value.length > MAX_LENGTH) return "/";
  if (!value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return "/";
  // Control characters (CR/LF header injection, tabs that browsers strip) are never valid.
  if (/[\u0000-\u001f\u007f\\]/.test(value)) return "/";
  let decoded: string;
  try {
    decoded = decodeURIComponent(value);
  } catch {
    return "/";
  }
  if (decoded.startsWith("//") || decoded.includes("\\") || /^\/[a-z][a-z0-9+.-]*:/i.test(decoded)) return "/";
  try {
    const parsed = new URL(value, "http://crimemap.invalid");
    if (parsed.origin !== "http://crimemap.invalid") return "/";
    if (parsed.pathname.startsWith("/bff/")) return "/";
  } catch {
    return "/";
  }
  return value;
}
