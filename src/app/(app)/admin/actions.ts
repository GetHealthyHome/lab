"use server";
import { revalidatePath } from "next/cache";
import { createClient } from "@/lib/supabase/server";

const str = (f: FormData, k: string) => (String(f.get(k) ?? "").trim() || null);

export async function addAsset(f: FormData) {
  const sb = await createClient();
  const { error } = await sb.from("assets").insert({
    name: str(f, "name")!, kind: str(f, "kind") ?? "individual", barcode: str(f, "barcode"), serial: str(f, "serial"),
    quantity: Number(f.get("quantity") || 1), kit_id: str(f, "kit_id"),
    home_location_id: str(f, "home_location_id"), location_id: str(f, "home_location_id"), notes: str(f, "notes"),
  });
  if (error) throw new Error(error.message);
  revalidatePath("/admin");
}

export async function addLocation(f: FormData) {
  const sb = await createClient();
  const { error } = await sb.from("locations").insert({ name: str(f, "name")!, code: str(f, "code")!, parent_id: str(f, "parent_id") });
  if (error) throw new Error(error.message);
  revalidatePath("/admin");
}

export async function addPerson(f: FormData) {
  const sb = await createClient();
  const { error } = await sb.from("people").insert({ name: str(f, "name")!, role: str(f, "role") ?? "student", email: str(f, "email"), badge_code: str(f, "badge_code") });
  if (error) throw new Error(error.message);
  revalidatePath("/admin");
}

export async function setAssetStatus(id: string, status: "available" | "lost" | "needs_repair") {
  const sb = await createClient();
  const { error } = await sb.from("assets").update({ status, condition: status === "available" ? "good" : undefined, holder_id: null, due_at: null }).eq("id", id);
  if (error) throw new Error(error.message);
  revalidatePath("/admin");
}
