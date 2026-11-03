import Link from "next/link";
import { createClient } from "@/lib/supabase/server";
import { addAsset, addLocation, addPerson, setAssetStatus } from "./actions";

export default async function AdminPage() {
  const sb = await createClient();
  const [{ data: assets }, { data: locations }, { data: people }] = await Promise.all([
    sb.from("assets").select("*,holder:people(name),location:locations!assets_location_id_fkey(name)").order("name"),
    sb.from("locations").select("*").order("name"),
    sb.from("people").select("*").order("name"),
  ]);
  const byId = new Map(locations?.map((l) => [l.id, l]));
  const path = (id: string | null): string => { const l = id ? byId.get(id) : undefined; return l ? (l.parent_id ? `${path(l.parent_id)} › ` : "") + l.name : ""; };
  const kits = assets?.filter((a) => a.kind === "kit") ?? [];

  return (
    <div className="flex flex-col gap-6 text-base">
      <h1 className="text-3xl font-black">Admin</h1>

      <section className="card flex flex-col gap-3">
        <h2 className="text-xl font-bold">Add a tool or kit</h2>
        <form action={addAsset} className="grid gap-2 md:grid-cols-2">
          <input className="input" name="name" placeholder="Name (e.g. Testo 310 combustion analyzer)" required />
          <select className="input" name="kind">
            <option value="individual">Individual (serial-tracked)</option>
            <option value="hand_tool">Hand tool / consumable (quantity)</option>
            <option value="kit">Kit (contains other tools)</option>
          </select>
          <input className="input" name="barcode" placeholder="Barcode / QR value (scan it here)" />
          <input className="input" name="serial" placeholder="Serial number" />
          <input className="input" name="quantity" type="number" min={1} defaultValue={1} placeholder="Quantity" />
          <select className="input" name="kit_id"><option value="">Not part of a kit</option>{kits.map((k) => <option key={k.id} value={k.id}>In kit: {k.name}</option>)}</select>
          <select className="input" name="home_location_id"><option value="">No home bin</option>{locations?.map((l) => <option key={l.id} value={l.id}>{path(l.id)}</option>)}</select>
          <input className="input" name="notes" placeholder="Notes" />
          <button className="btn btn-primary md:col-span-2">Add</button>
        </form>
      </section>

      <section className="card flex flex-col gap-3">
        <h2 className="text-xl font-bold">Add a storage location</h2>
        <p className="text-gray-600">Print a QR sticker whose value is the code. Nest drawers under toolboxes, carts under lockers.</p>
        <form action={addLocation} className="grid gap-2 md:grid-cols-3">
          <input className="input" name="name" placeholder="Name (e.g. Drawer 3)" required />
          <input className="input" name="code" placeholder="Label code (e.g. LOC-TB1-D3)" required />
          <select className="input" name="parent_id"><option value="">Top level</option>{locations?.map((l) => <option key={l.id} value={l.id}>{path(l.id)}</option>)}</select>
          <button className="btn btn-primary md:col-span-3">Add</button>
        </form>
      </section>

      <section className="card flex flex-col gap-3">
        <h2 className="text-xl font-bold">Add a person</h2>
        <p className="text-gray-600">Fill in a temporary password to give them a login; they must change it the first time they sign in. Leave it blank for students who only borrow tools.</p>
        <form action={addPerson} className="grid gap-2 md:grid-cols-3">
          <input className="input" name="name" placeholder="Full name" required />
          <select className="input" name="role"><option value="student">Student</option><option value="trainer">Trainer</option><option value="admin">Admin</option></select>
          <input className="input" name="badge_code" placeholder="Badge barcode" />
          <input className="input" name="email" type="email" placeholder="Email (login + reports)" />
          <input className="input" name="password" type="text" autoComplete="off" minLength={8} placeholder="Temporary password (8+ chars)" />
          <button className="btn btn-primary">Add</button>
        </form>
      </section>

      <section className="card">
        <h2 className="text-xl font-bold mb-2">Inventory ({assets?.length ?? 0})</h2>
        <table className="w-full text-left">
          <thead className="text-gray-600"><tr><th>Tool</th><th>Status</th><th>Holder / Location</th><th>Code</th><th></th></tr></thead>
          <tbody>
            {assets?.map((a) => (
              <tr key={a.id} className="border-t border-green-200">
                <td className="py-2"><Link href={`/assets/${a.id}`}>{a.kit_id ? "↳ " : ""}{a.name}</Link></td>
                <td>{a.status.replace("_", " ")}</td>
                <td className="text-gray-600">{a.holder?.name ?? a.location?.name ?? "—"}</td>
                <td className="font-mono text-gray-600">{a.barcode ?? ""}</td>
                <td className="text-right">
                  {a.status !== "available" && <form action={setAssetStatus.bind(null, a.id, "available")}><button className="text-green-700">mark available</button></form>}
                  {a.status === "available" && <form action={setAssetStatus.bind(null, a.id, "lost")}><button className="text-red-600">mark lost</button></form>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h2 className="text-xl font-bold mb-2">Locations</h2>
        <ul>{locations?.map((l) => <li key={l.id} className="py-1">{path(l.id)} <span className="font-mono text-gray-600">{l.code}</span></li>)}</ul>
      </section>
      <section className="card">
        <h2 className="text-xl font-bold mb-2">People</h2>
        <ul>{people?.map((p) => <li key={p.id} className="py-1">{p.name} <span className="text-gray-600">· {p.role}{p.auth_user_id ? " · has login" : ""}{p.email ? ` · ${p.email}` : ""}{p.badge_code ? ` · badge ${p.badge_code}` : ""}</span></li>)}</ul>
      </section>
    </div>
  );
}
