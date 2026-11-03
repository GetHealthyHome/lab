import Link from "next/link";
import { notFound } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

export default async function AssetPage({ params }: PageProps<"/assets/[id]">) {
  const { id } = await params;
  const supabase = await createClient();
  const [{ data: asset }, { data: children }, { data: history }] = await Promise.all([
    supabase.from("assets").select("*,holder:people(name),location:locations!assets_location_id_fkey(name),home:locations!assets_home_location_id_fkey(name)").eq("id", id).single(),
    supabase.from("assets").select("id,name,status").eq("kit_id", id).order("name"),
    supabase.from("movements").select("*,person:people(name),location:locations!movements_location_id_fkey(name)").eq("asset_id", id).order("created_at", { ascending: false }).limit(20),
  ]);
  if (!asset) notFound();
  const out = asset.status === "checked_out";
  return (
    <>
      <div className="card">
        <h1 className="text-3xl font-black">{asset.name}</h1>
        <p className="text-gray-700 text-lg mt-1">
          <span className={`badge ${out ? "bg-orange-500 text-white" : asset.status === "available" ? "bg-green-700 text-white" : "bg-red-600"}`}>{asset.status.replace("_", " ")}</span>
          {" "}· {asset.kind.replace("_", " ")} · condition {asset.condition}
        </p>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 mt-3 text-lg">
          <dt className="text-gray-600">Holder</dt><dd>{asset.holder?.name ?? "—"}</dd>
          <dt className="text-gray-600">Location</dt><dd>{asset.location?.name ?? "—"}</dd>
          <dt className="text-gray-600">Home bin</dt><dd>{asset.home?.name ?? "—"}</dd>
          <dt className="text-gray-600">Due</dt><dd>{asset.due_at ? new Date(asset.due_at).toLocaleString() : "—"}</dd>
          <dt className="text-gray-600">Barcode</dt><dd className="font-mono">{asset.barcode ?? "—"}</dd>
          <dt className="text-gray-600">Serial</dt><dd className="font-mono">{asset.serial ?? "—"}</dd>
          {asset.quantity > 1 && <><dt className="text-gray-600">Quantity</dt><dd>{asset.quantity}</dd></>}
        </dl>
        {asset.notes && <p className="mt-2 text-gray-700">{asset.notes}</p>}
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
          <ul>{children.map((c) => <li key={c.id} className="py-1 text-lg"><Link href={`/assets/${c.id}`}>{c.name}</Link> <span className="text-gray-600">· {c.status.replace("_", " ")}</span></li>)}</ul>
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
