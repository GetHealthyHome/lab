import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

// Refreshes the Supabase session cookie on every request and gates the app behind login.
export async function proxy(request: NextRequest) {
  let response = NextResponse.next({ request });
  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll: () => request.cookies.getAll(),
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
          response = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) => response.cookies.set(name, value, options));
        },
      },
    },
  );
  const { data: { user } } = await supabase.auth.getUser();
  const { pathname } = request.nextUrl;
  const isPublic = pathname.startsWith("/login") || pathname.startsWith("/auth") || pathname.startsWith("/api/reports");
  const url = request.nextUrl.clone();
  if (!user && !isPublic) {
    url.pathname = "/login";
    return NextResponse.redirect(url);
  }
  // Temporary passwords must be replaced before anything else is usable.
  if (user?.user_metadata?.must_change_password && !pathname.startsWith("/set-password") && !pathname.startsWith("/auth")) {
    url.pathname = "/set-password";
    return NextResponse.redirect(url);
  }
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|manifest.webmanifest|sw.js|icons/).*)"],
};
