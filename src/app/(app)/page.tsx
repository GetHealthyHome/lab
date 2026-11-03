import Link from "next/link";
import { createClient } from "@/lib/supabase/server";
import { Icon } from "@/components/Nav";
import { STATUS, statusOf, type StatusKey } from "@/lib/status";

export default async function Home() {
  const supabase = await createClient();
  const [{ data: { user } }, { data: assets }] = await Promise.all([
    supabase.auth.getUser(),
    supabase.from("assets").select("id,name,status,due_at,holder:people(name),location:locations!assets_location_id_fkey(name)").is("kit_id", null).order("due_at"),
  ]);
  const { data: me } = user ? await supabase.from("people").select("name").eq("auth_user_id", user.id).maybeSingle() : { data: null };
  const counts: Record<StatusKey, number> = { available: 0, out: 0, overdue: 0, repair: 0 };
  for (const a of assets ?? []) counts[statusOf(a)]++;
  const out = (assets ?? []).filter((a) => a.status === "checked_out");

  return (
    <>
      <h1 className="text-2xl font-black">Welcome, {me?.name?.split(" ")[0] ?? "there"}</h1>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {(Object.keys(STATUS) as StatusKey[]).map((k) => (
          <Link key={k} href={`/items?tab=${k}`} className={`${STATUS[k].bg} text-white rounded-2xl p-4 flex flex-col gap-1 shadow-sm active:scale-95 transition`}>
            <span className="text-4xl font-black">{counts[k]}</span>
            <span className="font-bold">{STATUS[k].label}</span>
          </Link>
        ))}
      </div>

      <section className="card flex flex-col gap-3">
        <h2 className="text-xl font-bold flex items-center gap-2"><Icon name="scan" /> Scan a tool</h2>
        <div className="grid grid-cols-2 gap-3">
          <Link href="/scan?mode=checkout" className="btn btn-primary min-h-20">Check out</Link>
          <Link href="/scan?mode=checkin" className="btn btn-secondary min-h-20">Check in</Link>
          <Link href="/scan?mode=lookup" className="btn btn-outline">Look up</Link>
          <Link href="/scan?mode=audit" className="btn btn-outline">Audit bin</Link>
          <Link href="/scan?mode=label" className="btn btn-outline">Attach label</Link>
          <Link href="/scan?mode=damage" className="btn btn-danger">Flag damage</Link>
        </div>
      </section>

      <section className="card">
        <h2 className="text-xl font-bold mb-2">Out right now ({out.length})</h2>
        <ul className="divide-y divide-gray-100">
          {out.map((a) => {
            const s = statusOf(a);
            return (
              <li key={a.id}>
                <Link href={`/assets/${a.id}`} className="flex justify-between items-center py-3 text-lg">
                  <span className="font-semibold">{a.name}</span>
                  <span className={`text-right ${STATUS[s].text}`}>{a.holder?.name ?? a.location?.name ?? "—"}{s === "overdue" ? " · LATE" : ""}</span>
                </Link>
              </li>
            );
          })}
          {out.length === 0 && <li className="text-gray-500 py-2">Everything is on the shelf.</li>}
        </ul>
      </section>
    </>
  );
}
