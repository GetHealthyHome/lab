"use client";
import Image from "next/image";
import Link from "next/link";
import { useState } from "react";
import { STATUS, statusOf, type StatusKey } from "@/lib/status";

export type ItemRow = {
  id: string; name: string; brand: string | null; kind: string; status: string; due_at: string | null; barcode: string | null; serial: string | null;
  notes: string | null; photo_url: string | null; kit_id: string | null; last_checked_out_at: string | null;
  holder: { name: string } | null; location: { name: string } | null;
};
type Tab = "all" | StatusKey;
const TABS: { key: Tab; label: string }[] = [{ key: "all", label: "All" }, { key: "available", label: "Available" }, { key: "out", label: "Out" }, { key: "overdue", label: "Overdue" }, { key: "repair", label: "Repair / lost" }];

export const fmtDate = (d: string | null) => (d ? new Date(d).toLocaleString([], { dateStyle: "short", timeStyle: "short" }) : "—");

export default function ItemList({ items, initialTab }: { items: ItemRow[]; initialTab: Tab }) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const [q, setQ] = useState("");
  const needle = q.trim().toLowerCase();
  const shown = items.filter((a) =>
    (tab === "all" || statusOf(a) === tab) &&
    (!needle || [a.name, a.brand, a.barcode, a.serial, a.holder?.name, a.location?.name].some((v) => v?.toLowerCase().includes(needle))),
  );
  return (
    <>
      <input className="input" type="search" placeholder="Search tools, brands, labels, people, bins" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="flex gap-2 overflow-x-auto -mx-4 px-4 pb-1">
        {TABS.map((t) => <button key={t.key} className={`tab ${tab === t.key ? "tab-on" : "tab-off"}`} onClick={() => setTab(t.key)}>{t.label}</button>)}
      </div>
      <p className="text-gray-500">{shown.length} of {items.length}</p>
      <ul className="grid gap-3 md:grid-cols-2">
        {shown.map((a) => {
          const s = statusOf(a);
          return (
            <li key={a.id}>
              <Link href={`/assets/${a.id}`} className={`card flex flex-col gap-3 border-l-8 ${STATUS[s].border} active:scale-[0.99] transition`}>
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-xl font-bold leading-tight">{a.kit_id ? "↳ " : ""}{a.name}</p>
                    <p className="text-gray-600">{a.brand ?? a.kind.replace("_", " ")}</p>
                  </div>
                  <span className={`badge text-white shrink-0 ${STATUS[s].bg}`}>{STATUS[s].label}</span>
                </div>
                <div className="flex gap-4">
                  {a.photo_url
                    ? <Image src={a.photo_url} alt="" width={128} height={128} className="w-32 h-32 rounded-xl object-cover bg-gray-100 shrink-0" />
                    : <div className="w-32 h-32 rounded-xl bg-gray-100 shrink-0 flex items-center justify-center text-4xl">🔧</div>}
                  <dl className="flex-1 min-w-0 text-sm grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 content-start">
                    <dt className="text-gray-500">Last out</dt><dd className="truncate">{fmtDate(a.last_checked_out_at)}</dd>
                    <dt className="text-gray-500">Serial</dt><dd className="font-mono truncate">{a.serial ?? "—"}</dd>
                    <dt className="text-gray-500">Location</dt><dd className="truncate">{a.holder?.name ? `👤 ${a.holder.name}` : a.location?.name ?? "—"}</dd>
                    <dt className="text-gray-500">Barcode</dt><dd className="font-mono truncate">{a.barcode ?? "no label"}</dd>
                    <dt className="text-gray-500">Notes</dt><dd className="truncate">{a.notes ?? "—"}</dd>
                  </dl>
                </div>
              </Link>
            </li>
          );
        })}
        {shown.length === 0 && <li className="text-gray-500">Nothing matches.</li>}
      </ul>
    </>
  );
}
