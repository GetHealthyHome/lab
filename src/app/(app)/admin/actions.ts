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
  const name = str(f, "name")!, role = str(f, "role") ?? "student", email = str(f, "email"), password = str(f, "password");
  if (password) {
    // A login: created by the admin-create-user edge function (holds the service-role key) with a temporary password.
    if (!email) throw new Error("A login needs an email address");
    const { data: { session } } = await sb.auth.getSession();
    const res = await fetch(`${process.env.NEXT_PUBLIC_SUPABASE_URL}/functions/v1/admin-create-user`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${session?.access_token}` },
      body: JSON.stringify({ name, email, role, password }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error ?? `Could not create login (${res.status})`);
    // the auth trigger inserted/updated the people row; attach the badge if given
    if (str(f, "badge_code")) {
      const { error } = await sb.from("people").update({ badge_code: str(f, "badge_code") }).eq("email", email);
      if (error) throw new Error(`Login created, but the badge was not saved (is it already assigned?): ${error.message}`);
    }
  } else {
    const { error } = await sb.from("people").insert({ name, role, email, badge_code: str(f, "badge_code") });
    if (error) throw new Error(error.message);
  }
  revalidatePath("/admin");
}

export async function setAssetStatus(id: string, status: "available" | "lost" | "needs_repair") {
  const sb = await createClient();
  const { error } = await sb.from("assets").update({ status, condition: status === "available" ? "good" : undefined, holder_id: null, due_at: null }).eq("id", id);
  if (error) throw new Error(error.message);
  revalidatePath("/admin");
}
