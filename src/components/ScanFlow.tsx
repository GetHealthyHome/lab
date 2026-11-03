"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Scanner from "@/components/Scanner";
import { createClient } from "@/lib/supabase/client";
import { flushOutbox, pendingCount, recordMovement, refreshSnapshot, type Snapshot } from "@/lib/offline";
import type { Asset, Location, Person } from "@/lib/database.types";

export type Mode = "checkout" | "checkin" | "lookup" | "audit" | "damage" | "label";
const TITLES: Record<Mode, string> = { checkout: "Check out", checkin: "Check in", lookup: "Look up", audit: "Audit a bin", damage: "Flag damage", label: "Attach label" };

function endOfToday() {
  const d = new Date(); d.setHours(23, 59, 0, 0); return d;
}
function toLocalInput(d: Date) {
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

export default function ScanFlow({ mode, initialCode, assetId }: { mode: Mode; initialCode?: string; assetId?: string }) {
  const router = useRouter();
  const [snap, setSnap] = useState<Snapshot>();
  const [asset, setAsset] = useState<Asset | null>(null);
  const [person, setPerson] = useState<Person | null>(null); // sticky: scan a badge once, check out many tools
  const [bin, setBin] = useState<Location | null>(null); // sticky: scan a shelf once, deposit many tools
  const [unknown, setUnknown] = useState<string | null>(null); // a pre-printed label nobody owns yet
  const [labelSearch, setLabelSearch] = useState("");
  const [missing, setMissing] = useState<Set<string>>(new Set());
  const [condition, setCondition] = useState<"good" | "fair" | "damaged">("good");
  const [note, setNote] = useState("");
  const [due, setDue] = useState(toLocalInput(endOfToday()));
  const [seen, setSeen] = useState<Set<string>>(new Set());
  const [toast, setToast] = useState<string | null>(null);
  const [pending, setPending] = useState(0);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const sync = async () => { await flushOutbox(); setSnap(await refreshSnapshot()); setPending(await pendingCount()); };
    sync();
    window.addEventListener("online", sync);
    return () => window.removeEventListener("online", sync);
  }, []);

  function notify(m: string) { setToast(m); setTimeout(() => setToast(null), 2500); }

  const target = assetId ? snap?.assets.find((x) => x.id === assetId) : undefined;

  async function attachLabel(id: string, code: string) {
    setBusy(true);
    const { error } = await createClient().from("assets").update({ barcode: code }).eq("id", id);
    setBusy(false);
    if (error) return notify(error.message);
    const name = snap?.assets.find((x) => x.id === id)?.name ?? "tool";
    notify(`Label ${code} attached to ${name}`);
    setUnknown(null); setLabelSearch("");
    setSnap(await refreshSnapshot());
    if (mode === "label") router.push(`/assets/${id}`);
  }

  const resolve = useCallback((code: string) => {
    if (!snap) return;
    const a = snap.assets.find((x) => x.barcode === code || x.id === code || x.serial === code);
    const l = snap.locations.find((x) => x.code === code || x.id === code);
    const p = snap.people.find((x) => x.badge_code === code || x.id === code);
    if (mode === "label") {
      if (a) return notify(`That label already belongs to ${a.name}`);
      if (l || p) return notify("That is a bin or badge label, not a tool label");
      if (assetId) return void attachLabel(assetId, code);
      return setUnknown(code);
    }
    if (a) {
      if (mode === "lookup") return router.push(`/assets/${a.id}`);
      if (mode === "audit" && bin) {
        recordMovement({ p_asset_id: a.id, p_action: "audit", p_location_id: bin.id }).then((r) => notify(`${a.name} ✓${r === "queued" ? " (offline, will sync)" : ""}`));
        setSeen((s) => new Set(s).add(a.id));
        return;
      }
      setMissing(new Set()); setCondition("good"); setNote("");
      return setAsset(a);
    }
    if (l) return (setBin(l), notify(`Bin: ${l.name}`));
    if (p) return (setPerson(p), notify(`Person: ${p.name}`));
    setUnknown(code);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- attachLabel only reads state through setters
  }, [snap, mode, bin, router, assetId]);

  // Asset page links here with ?code=; resolve it once the snapshot is in.
  const initialDone = useRef(false);
  useEffect(() => {
    if (initialCode && snap && !initialDone.current) {
      initialDone.current = true;
      resolve(initialCode);
    }
  }, [initialCode, snap, resolve]);

  async function confirm() {
    if (!asset) return;
    setBusy(true);
    try {
      const action = mode === "checkout" ? "check_out" : mode === "checkin" ? "check_in" : "damage";
      const r = await recordMovement({
        p_asset_id: asset.id, p_action: action,
        p_person_id: person?.id, p_location_id: bin?.id,
        p_condition: mode === "checkin" ? condition : mode === "damage" ? "damaged" : undefined,
        p_note: note || undefined,
        p_due_at: mode === "checkout" ? new Date(due).toISOString() : undefined,
        p_missing_ids: [...missing],
      });
      notify(`${TITLES[mode]}: ${asset.name}${r === "queued" ? " (offline, will sync)" : ""}`);
      setAsset(null);
      setPending(await pendingCount());
      if (initialCode) router.push(`/assets/${asset.id}`); else setSnap(await refreshSnapshot());
    } catch (e) { notify((e as Error).message); } finally { setBusy(false); }
  }

  const children = asset?.kind === "kit" ? snap?.assets.filter((x) => x.kit_id === asset.id) ?? [] : [];
  const expected = bin ? snap?.assets.filter((x) => x.location_id === bin.id && x.status !== "checked_out") ?? [] : [];
  const canConfirm = mode !== "checkout" || person || bin;
  const needle = labelSearch.trim().toLowerCase();
  const unlabeled = (snap?.assets ?? []).filter((x) => !x.barcode && (!needle || x.name.toLowerCase().includes(needle))).slice(0, 12);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-black">{TITLES[mode]}{target ? `: ${target.name}` : ""}</h1>
        {pending > 0 && <span className="badge bg-orange-500 text-white">{pending} waiting to sync</span>}
      </div>
      <div className="flex gap-2 flex-wrap text-lg">
        {person && <button className="badge bg-blue-600 text-white" onClick={() => setPerson(null)}>👤 {person.name} ✕</button>}
        {bin && <button className="badge bg-green-700 text-white" onClick={() => setBin(null)}>📦 {bin.name} ✕</button>}
      </div>

      {!asset && !unknown && <Scanner onCode={resolve} />}
      {mode === "label" && !unknown && <p className="card text-gray-700 text-lg">{target ? "Point the camera at the pre-printed label you want to stick on this tool." : "Scan a pre-printed label, then pick the tool it belongs to."}</p>}
      {!asset && mode === "checkout" && !person && !bin && snap && (
        <div className="card flex flex-col gap-2">
          <p className="text-gray-700">Who is taking it? (or scan a badge / truck bin)</p>
          <div className="grid grid-cols-2 gap-2">
            {snap.people.map((p) => <button key={p.id} className="btn btn-outline" onClick={() => setPerson(p)}>{p.name}</button>)}
          </div>
        </div>
      )}
      {!asset && mode === "audit" && !bin && <p className="card text-gray-700 text-lg">Scan the shelf or drawer label first, then scan every tool in it.</p>}
      {!asset && mode === "audit" && bin && (
        <ul className="card flex flex-col gap-1 text-lg">
          {expected.map((a) => <li key={a.id}>{seen.has(a.id) ? "✅" : "⬜"} {a.name}</li>)}
          {expected.length === 0 && <li className="text-gray-500">Nothing is recorded in this bin yet.</li>}
        </ul>
      )}

      {unknown && (
        <div className="card flex flex-col gap-3">
          <p className="text-xl font-bold">New label: <span className="font-mono">{unknown}</span></p>
          <p className="text-gray-700">Nothing wears this label yet. Which tool is it for?</p>
          <input className="input" placeholder="Search tools without a label" value={labelSearch} onChange={(e) => setLabelSearch(e.target.value)} autoFocus />
          <div className="grid grid-cols-1 gap-2 max-h-72 overflow-y-auto">
            {unlabeled.map((a) => <button key={a.id} className="btn btn-outline min-h-14 text-lg justify-start" disabled={busy} onClick={() => attachLabel(a.id, unknown)}>{a.name}</button>)}
            {unlabeled.length === 0 && <p className="text-gray-500">No unlabeled tools match. Add the tool on the Admin page first.</p>}
          </div>
          <button className="btn btn-secondary" onClick={() => { setUnknown(null); setLabelSearch(""); }}>Cancel</button>
        </div>
      )}

      {asset && (
        <div className="card flex flex-col gap-4">
          <div>
            <p className="text-2xl font-bold">{asset.name}</p>
            <p className="text-gray-600">{asset.kind.replace("_", " ")} · {asset.status.replace("_", " ")}{asset.serial ? ` · SN ${asset.serial}` : ""}</p>
          </div>
          {children.length > 0 && (
            <div>
              <p className="font-bold mb-1">Kit contents (untick anything missing)</p>
              {children.map((c) => (
                <label key={c.id} className="flex items-center gap-3 py-2 text-lg">
                  <input type="checkbox" className="w-7 h-7" checked={!missing.has(c.id)}
                    onChange={(e) => setMissing((m) => { const n = new Set(m); if (e.target.checked) n.delete(c.id); else n.add(c.id); return n; })} />
                  {c.name}
                </label>
              ))}
            </div>
          )}
          {mode === "checkout" && (
            <>
              {!person && !bin && snap && (
                <div className="grid grid-cols-2 gap-2">
                  {snap.people.map((p) => <button key={p.id} className="btn btn-outline" onClick={() => setPerson(p)}>{p.name}</button>)}
                </div>
              )}
              <label className="flex flex-col gap-1">Due back
                <input className="input" type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} />
              </label>
            </>
          )}
          {mode === "checkin" && (
            <div className="grid grid-cols-3 gap-2">
              {(["good", "fair", "damaged"] as const).map((c) => (
                <button key={c} className={`btn ${condition === c ? "btn-primary" : "btn-outline"}`} onClick={() => setCondition(c)}>{c}</button>
              ))}
            </div>
          )}
          {mode === "checkin" && !bin && <p className="text-gray-600">Scan a shelf label after confirming to record where it went, or it returns to its home bin.</p>}
          {(mode === "damage" || condition === "damaged") && (
            <input className="input" placeholder="What is wrong?" value={note} onChange={(e) => setNote(e.target.value)} />
          )}
          <div className="grid grid-cols-2 gap-2">
            <button className="btn btn-outline" onClick={() => setAsset(null)} disabled={busy}>Cancel</button>
            <button className={`btn ${mode === "damage" ? "btn-danger" : "btn-primary"}`} onClick={confirm} disabled={busy || !canConfirm}>Confirm</button>
          </div>
        </div>
      )}

      {toast && <div className="fixed bottom-24 md:bottom-6 left-4 right-4 md:left-auto md:w-96 rounded-2xl bg-green-800 text-white p-4 text-xl font-bold text-center shadow-xl z-30">{toast}</div>}
    </div>
  );
}
