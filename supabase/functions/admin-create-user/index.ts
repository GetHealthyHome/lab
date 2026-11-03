// Lets a signed-in admin create a login (email + temporary password) for a trainer, admin or student.
// Runs here so the service-role key never leaves Supabase. The new user must change the password on first sign-in.
import { createClient } from "npm:@supabase/supabase-js@2";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") return json({ error: "POST only" }, 405);
  const token = (req.headers.get("Authorization") ?? "").replace(/^Bearer\s+/i, "");
  const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);

  const { data: { user: caller } } = await admin.auth.getUser(token);
  if (!caller) return json({ error: "Not signed in" }, 401);
  const { data: me } = await admin.from("people").select("role").eq("auth_user_id", caller.id).maybeSingle();
  if (me?.role !== "admin") return json({ error: "Only admins can create logins" }, 403);

  const { email, password, name, role } = await req.json().catch(() => ({}));
  if (!email || !name || typeof password !== "string" || password.length < 8) {
    return json({ error: "Name, email and a temporary password of at least 8 characters are required" }, 400);
  }
  if (!["admin", "trainer", "student"].includes(role)) return json({ error: "Bad role" }, 400);

  const { error } = await admin.auth.admin.createUser({
    email, password, email_confirm: true,
    user_metadata: { full_name: name, role, must_change_password: true },
  });
  if (error) return json({ error: error.message }, 400);
  return json({ ok: true });
});
