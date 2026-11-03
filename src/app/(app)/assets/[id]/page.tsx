import Image from "next/image";
import Link from "next/link";
import { notFound } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { STATUS, statusOf } from "@/lib/status";

export default async function AssetPage({ params }: PageProps<"/assets/[id]">) {
  const { id } = await params;
  const supabase = await createClient();
  const [{ data: asset }, { data: children }, { data: history }] = await Promise.all([
    supabase.from("assets").select("*,holder:people(name),location:locations!assets_location_id_fkey(name),home:locations!assets_home_location_id_fkey(name)").eq("id", id).single(),
    supabase.from("assets").select("id,name,status").eq("kit_id", id).order("name"),
    supabase.from("movements").select("*,person:people(name),location:locations!movements_location_id_fkey(name)").eq("asset_id", id).order("created_at", { ascending: false }).limit(20),
  ]);
  if (!asset) notFound();
  const s = statusOf(asset);
  const out = asset.status === "checked_out";
  return (
    <>
      <Link href="/items" className="text-green-800 font-semibold">‹ Items</Link>
      <div className={`card border-l-8 ${STATUS[s].border} flex flex-col gap-4`}>
        <div className="flex items-start justify-between gap-2">
          <div>
            <h1 className="text-3xl font-black leading-tight">{asset.name}</h1>
            <p className="text-gray-600 text-lg">{asset.brand ?? asset.kind.replace("_", " ")}</p>
          </div>
          <span className={`badge text-white shrink-0 ${STATUS[s].bg}`}>{STATUS[s].label}</span>
        </div>
        <div className="flex flex-col sm:flex-row gap-4">
          {asset.photo_url
            ? <Image src={asset.photo_url} alt={asset.name} width={320} height={320} className="w-full sm:w-64 h-64 rounded-xl object-cover bg-gray-100 shrink-0" />
            : <div className="w-full sm:w-64 h-40 sm:h-64 rounded-xl bg-gray-100 flex items-center justify-center text-6xl shrink-0">🔧</div>}
          <dl className="flex-1 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-lg content-start">
            <dt className="text-gray-500">Last time checked out</dt><dd>{asset.last_checked_out_at ? new Date(asset.last_checked_out_at).toLocaleString() : "Never"}</dd>
            <dt className="text-gray-500">Serial number</dt><dd className="font-mono">{asset.serial ?? "—"}</dd>
            <dt className="text-gray-500">Location</dt><dd>{asset.location?.name ?? "—"}{asset.home?.name && asset.home.name !== asset.location?.name ? <span className="text-gray-500"> (home: {asset.home.name})</span> : ""}</dd>
            <dt className="text-gray-500">Barcode</dt><dd className="font-mono">{asset.barcode ?? <Link href={`/scan?mode=label&asset=${asset.id}`} className="text-orange-600 font-sans font-bold">Attach a label</Link>}</dd>
            <dt className="text-gray-500">Notes</dt><dd>{asset.notes ?? "—"}</dd>
            {out && <><dt className="text-gray-500">Holder</dt><dd>{asset.holder?.name ?? "—"}</dd><dt className="text-gray-500">Due back</dt><dd>{asset.due_at ? new Date(asset.due_at).toLocaleString() : "—"}</dd></>}
            <dt className="text-gray-500">Condition</dt><dd>{asset.condition}{asset.quantity > 1 ? ` · qty ${asset.quantity}` : ""}</dd>
          </dl>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {out
          ? <Link href={`/scan?mode=checkin&code=${asset.id}`} className="btn btn-primary">Check in</Link>
          : <Link href={`/scan?mode=checkout&code=${asset.id}`} className="btn btn-primary">Check out</Link>}
        <Link href={`/scan?mode=damage&code=${asset.id}`} className="btn btn-danger">Flag damage</Link>
      </div>
      {children && children.length > 0 && (
        <div className="card">
          <h2 className="font-bold text-xl mb-2">Kit contents</h2>
          <ul>{children.map((c) => <li key={c.id} className="py-1 text-lg"><Link href={`/assets/${c.id}`}>{c.name}</Link> <span className="text-gray-500">· {c.status.replace("_", " ")}</span></li>)}</ul>
        </div>
      )}
      <div className="card">
        <h2 className="font-bold text-xl mb-2">History</h2>
        <ul className="flex flex-col gap-1">
          {history?.map((m) => (
            <li key={m.id} className="text-gray-700">
              <span className="text-gray-500">{new Date(m.created_at).toLocaleString()}</span> · {m.action.replace("_", " ")}
              {m.person?.name ? ` · ${m.person.name}` : ""}{m.location?.name ? ` · ${m.location.name}` : ""}{m.condition ? ` · ${m.condition}` : ""}{m.note ? ` · ${m.note}` : ""}
            </li>
          ))}
          {history?.length === 0 && <li className="text-gray-500">No activity yet.</li>}
        </ul>
      </div>
    </>
  );
}
