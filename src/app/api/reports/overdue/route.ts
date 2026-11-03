import { createClient } from "@supabase/supabase-js";
import { NextResponse, type NextRequest } from "next/server";
import type { Database } from "@/lib/database.types";

// Nightly (vercel.json cron): list every tool still out past its due time and email admins + trainers.
export async function GET(request: NextRequest) {
  if (request.headers.get("authorization") !== `Bearer ${process.env.CRON_SECRET}`) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const sb = createClient<Database>(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.SUPABASE_SERVICE_ROLE_KEY!);
  const [{ data: overdue }, { data: staff }] = await Promise.all([
    sb.from("assets").select("name,due_at,holder:people(name),location:locations!assets_location_id_fkey(name)")
      .eq("status", "checked_out").is("kit_id", null).lt("due_at", new Date().toISOString()).order("due_at"),
    sb.from("people").select("email").in("role", ["admin", "trainer"]).eq("active", true).not("email", "is", null),
  ]);
  const rows = overdue ?? [];
  const to = (staff ?? []).map((s) => s.email!).filter(Boolean);
  let emailed = false;
  if (rows.length && to.length && process.env.RESEND_API_KEY) {
    const lines = rows.map((r) => `• ${r.name} — ${r.holder?.name ?? r.location?.name ?? "unknown"} — due ${new Date(r.due_at!).toLocaleString()}`);
    const res = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: { Authorization: `Bearer ${process.env.RESEND_API_KEY}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        from: process.env.REPORT_FROM_EMAIL ?? "tools@example.com", to,
        subject: `${rows.length} tool${rows.length === 1 ? "" : "s"} not checked back in`,
        text: `These tools are still out past their due time:\n\n${lines.join("\n")}\n`,
      }),
    });
    emailed = res.ok;
  }
  return NextResponse.json({ overdue: rows, recipients: to, emailed });
}
