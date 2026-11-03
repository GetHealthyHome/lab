import Link from "next/link";
import { createClient } from "@/lib/supabase/server";
import ItemList, { type ItemRow } from "@/components/ItemList";

export default async function ItemsPage({ searchParams }: PageProps<"/items">) {
  const { tab } = await searchParams;
  const sb = await createClient();
  const { data } = await sb.from("assets")
    .select("id,name,kind,status,due_at,barcode,serial,photo_url,kit_id,holder:people(name),location:locations!assets_location_id_fkey(name)")
    .order("name");
  const initial = ["available", "out", "overdue", "repair"].includes(String(tab)) ? (tab as "available") : "all";
  return (
    <>
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-black">Items</h1>
        <Link href="/admin" className="btn btn-primary min-h-12 text-base">+ Add item</Link>
      </div>
      <ItemList items={(data ?? []) as ItemRow[]} initialTab={initial} />
    </>
  );
}
