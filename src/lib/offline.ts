import { get, set } from "idb-keyval";
import { createClient } from "@/lib/supabase/client";
import type { Asset, Location, MovementArgs, Person } from "@/lib/database.types";

export type Snapshot = { assets: Asset[]; people: Person[]; locations: Location[]; at: number };

// A local copy of the three lookup tables so scans resolve instantly, even in a dead zone.
export async function refreshSnapshot(): Promise<Snapshot | undefined> {
  if (!navigator.onLine) return get<Snapshot>("snapshot");
  const sb = createClient();
  const [a, p, l] = await Promise.all([
    sb.from("assets").select("*").order("name"),
    sb.from("people").select("*").eq("active", true).order("name"),
    sb.from("locations").select("*").order("name"),
  ]);
  if (a.error || p.error || l.error) return get<Snapshot>("snapshot");
  const snap = { assets: a.data, people: p.data, locations: l.data, at: Date.now() };
  await set("snapshot", snap);
  return snap;
}

// Scan actions made offline wait here and replay in order when the network returns.
export async function recordMovement(args: MovementArgs): Promise<"saved" | "queued"> {
  if (navigator.onLine) {
    const { error } = await createClient().rpc("record_movement", args);
    if (!error) return "saved";
    if (!/fetch|network/i.test(error.message)) throw new Error(error.message);
  }
  const queue = (await get<MovementArgs[]>("outbox")) ?? [];
  await set("outbox", [...queue, args]);
  return "queued";
}

export async function flushOutbox(): Promise<number> {
  const queue = (await get<MovementArgs[]>("outbox")) ?? [];
  const sb = createClient();
  let done = 0;
  for (const args of queue) {
    const { error } = await sb.rpc("record_movement", args);
    if (error) break; // keep order; retry the rest next time
    done++;
  }
  await set("outbox", queue.slice(done));
  return queue.length - done;
}

export async function pendingCount() {
  return ((await get<MovementArgs[]>("outbox")) ?? []).length;
}
