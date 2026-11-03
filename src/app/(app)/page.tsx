import Link from "next/link";
import { createClient } from "@/lib/supabase/server";

const isLate = (due: string | null) => !!due && new Date(due).getTime() < Date.now();

export default async function Home() {
  const supabase = await createClient();
  const { data: out } = await supabase
    .from("assets").select("id,name,due_at,holder:people(name),location:locations!assets_location_id_fkey(name)")
    .eq("status", "checked_out").is("kit_id", null).order("due_at");
  return (
    <>
      <div className="grid grid-cols-2 gap-3">
        <Link href="/scan?mode=checkout" className="btn btn-primary min-h-28">Check out</Link>
        <Link href="/scan?mode=checkin" className="btn btn-primary min-h-28">Check in</Link>
        <Link href="/scan?mode=lookup" className="btn btn-secondary min-h-20">Look up</Link>
        <Link href="/scan?mode=audit" className="btn btn-secondary min-h-20">Audit bin</Link>
        <Link href="/scan?mode=damage" className="btn btn-danger min-h-16 col-span-2 text-lg">Flag damage / repair</Link>
      </div>
      <h2 className="text-xl font-bold mt-2">Out right now ({out?.length ?? 0})</h2>
      <ul className="flex flex-col gap-2">
        {out?.map((a) => {
          const late = isLate(a.due_at);
          return (
            <li key={a.id}>
              <Link href={`/assets/${a.id}`} className={`card flex justify-between text-lg ${late ? "border-red-500" : ""}`}>
                <span>{a.name}</span>
                <span className="text-gray-600">{a.holder?.name ?? a.location?.name ?? "—"}{late ? " · LATE" : ""}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </>
  );
}
