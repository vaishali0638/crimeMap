import { NextResponse, type NextRequest } from "next/server";
import { authenticateDemoUser } from "@/lib/demo-auth";
import { safeReturnTo } from "@/lib/redirect";
import { cookieOptions, CSRF_COOKIE, SESSION_COOKIE } from "@/lib/session";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  let username = "demo.analyst";
  let returnTo = "/";

  const contentType = request.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    try {
      const body = await request.json();
      if (body.username && typeof body.username === "string") username = body.username;
      if (body.returnTo && typeof body.returnTo === "string") returnTo = body.returnTo;
    } catch {
      // fallback
    }
  } else {
    try {
      const formData = await request.formData();
      const u = formData.get("username");
      if (u && typeof u === "string") username = u;
      const r = formData.get("returnTo");
      if (r && typeof r === "string") returnTo = r;
    } catch {
      // fallback
    }
  }

  const destination = safeReturnTo(returnTo);

  try {
    const result = await authenticateDemoUser(username);
    const redirectUrl = new URL(destination, request.url);
    const response = NextResponse.redirect(redirectUrl, 303);
    
    response.cookies.set(SESSION_COOKIE, result.sessionId, cookieOptions(result.maxAge));
    response.cookies.set(CSRF_COOKIE, result.csrfToken, cookieOptions(result.maxAge, false));
    response.headers.set("Cache-Control", "no-store");
    return response;
  } catch (error) {
    const message = error instanceof Error ? error.message : "Authentication failed";
    const failUrl = new URL("/login", request.url);
    failUrl.searchParams.set("error", message);
    failUrl.searchParams.set("returnTo", destination);
    return NextResponse.redirect(failUrl, 303);
  }
}
