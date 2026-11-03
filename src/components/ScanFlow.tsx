"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Scanner from "@/components/Scanner";
import { flushOutbox, pendingCount, recordMovement, refreshSnapshot, type Snapshot } from "@/lib/offline";
import type { Asset, Location, Person } from "@/lib/database.types";

export type Mode = "checkout" | "checkin" | "lookup" | "audit" | "damage";
const TITLES: Record<Mode, string> = { checkout: "Check out", checkin: "Check in", lookup: "Look up", audit: "Audit a bin", damage: "Flag damage" };

function endOfToday() {
  const d = new Date(); d.setHours(23, 59, 0, 0); return d;
}
function toLocalInput(d: Date) {
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

export default function ScanFlow({ mode, initialCode }: { mode: Mode; initialCode?: string }) {
  const router = useRouter();
  const [snap, setSnap] = useState<Snapshot>();
  const [asset, setAsset] = useState<Asset | null>(null);
  const [person, setPerson] = useState<Person | null>(null); // sticky: scan a badge once, check out many tools
  const [bin, setBin] = useState<Location | null>(null); // sticky: scan a shelf once, deposit many tools
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

  const resolve = useCallback((code: string) => {
    if (!snap) return;
    const a = snap.assets.find((x) => x.barcode === code || x.id === code || x.serial === code);
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
    const l = snap.locations.find((x) => x.code === code || x.id === code);
    if (l) return (setBin(l), notify(`Bin: ${l.name}`));
    const p = snap.people.find((x) => x.badge_code === code || x.id === code);
    if (p) return (setPerson(p), notify(`Person: ${p.name}`));
    notify(`Unknown code: ${code}`);
  }, [snap, mode, bin, router]);

  // Asset page links here with ?code=; resolve it once the snapshot is in.
  const initialDone = useRef(false);
  useEffect(() => {
    if (initialCode && snap && !initialDone.current) {
      initialDone.current = true;
      resolve(initialCode);
    }
  }, [initialCode, snap, resolve]);

  function notify(m: string) { setToast(m); setTimeout(() => setToast(null), 2500); }

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

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-black">{TITLES[mode]}</h1>
        {pending > 0 && <span className="badge bg-amber-500 text-black">{pending} waiting to sync</span>}
      </div>
      <div className="flex gap-2 flex-wrap text-lg">
        {person && <button className="badge bg-sky-600" onClick={() => setPerson(null)}>👤 {person.name} ✕</button>}
        {bin && <button className="badge bg-emerald-600" onClick={() => setBin(null)}>📦 {bin.name} ✕</button>}
      </div>

      {!asset && <Scanner onCode={resolve} />}
      {!asset && mode === "checkout" && !person && !bin && snap && (
        <div className="card flex flex-col gap-2">
          <p className="text-slate-300">Who is taking it? (or scan a badge / truck bin)</p>
          <div className="grid grid-cols-2 gap-2">
            {snap.people.map((p) => <button key={p.id} className="btn btn-secondary" onClick={() => setPerson(p)}>{p.name}</button>)}
          </div>
        </div>
      )}
      {!asset && mode === "audit" && !bin && <p className="card text-slate-300 text-lg">Scan the shelf or drawer label first, then scan every tool in it.</p>}
      {!asset && mode === "audit" && bin && (
        <ul className="card flex flex-col gap-1 text-lg">
          {expected.map((a) => <li key={a.id}>{seen.has(a.id) ? "✅" : "⬜"} {a.name}</li>)}
          {expected.length === 0 && <li className="text-slate-400">Nothing is recorded in this bin yet.</li>}
        </ul>
      )}

      {asset && (
        <div className="card flex flex-col gap-4">
          <div>
            <p className="text-2xl font-bold">{asset.name}</p>
            <p className="text-slate-400">{asset.kind} · {asset.status.replace("_", " ")}{asset.serial ? ` · SN ${asset.serial}` : ""}</p>
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
                  {snap.people.map((p) => <button key={p.id} className="btn btn-secondary" onClick={() => setPerson(p)}>{p.name}</button>)}
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
                <button key={c} className={`btn ${condition === c ? "btn-primary" : "btn-secondary"}`} onClick={() => setCondition(c)}>{c}</button>
              ))}
            </div>
          )}
          {mode === "checkin" && !bin && <p className="text-slate-400">Scan a shelf label after confirming to record where it went, or it returns to its home bin.</p>}
          {(mode === "damage" || condition === "damaged") && (
            <input className="input" placeholder="What is wrong?" value={note} onChange={(e) => setNote(e.target.value)} />
          )}
          <div className="grid grid-cols-2 gap-2">
            <button className="btn btn-secondary" onClick={() => setAsset(null)} disabled={busy}>Cancel</button>
            <button className={`btn ${mode === "damage" ? "btn-danger" : "btn-primary"}`} onClick={confirm} disabled={busy || !canConfirm}>Confirm</button>
          </div>
        </div>
      )}

      {toast && <div className="fixed bottom-6 left-4 right-4 rounded-2xl bg-slate-100 text-black p-4 text-xl font-bold text-center shadow-xl">{toast}</div>}
    </div>
  );
}
