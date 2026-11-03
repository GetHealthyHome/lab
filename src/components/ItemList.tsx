"use client";
import Image from "next/image";
import Link from "next/link";
import { useState } from "react";
import { STATUS, statusOf, type StatusKey } from "@/lib/status";

export type ItemRow = {
  id: string; name: string; kind: string; status: string; due_at: string | null; barcode: string | null; serial: string | null;
  photo_url: string | null; kit_id: string | null;
  holder: { name: string } | null; location: { name: string } | null;
};
type Tab = "all" | StatusKey;
const TABS: { key: Tab; label: string }[] = [{ key: "all", label: "All" }, { key: "available", label: "Available" }, { key: "out", label: "Out" }, { key: "overdue", label: "Overdue" }, { key: "repair", label: "Repair / lost" }];

export default function ItemList({ items, initialTab }: { items: ItemRow[]; initialTab: Tab }) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const [q, setQ] = useState("");
  const needle = q.trim().toLowerCase();
  const shown = items.filter((a) =>
    (tab === "all" || statusOf(a) === tab) &&
    (!needle || [a.name, a.barcode, a.serial, a.holder?.name, a.location?.name].some((v) => v?.toLowerCase().includes(needle))),
  );
  return (
    <>
      <input className="input" type="search" placeholder="Search tools, labels, people, bins" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="flex gap-2 overflow-x-auto -mx-4 px-4 pb-1">
        {TABS.map((t) => <button key={t.key} className={`tab ${tab === t.key ? "tab-on" : "tab-off"}`} onClick={() => setTab(t.key)}>{t.label}</button>)}
      </div>
      <p className="text-gray-500">{shown.length} of {items.length}</p>
      <ul className="grid gap-3 md:grid-cols-2">
        {shown.map((a) => {
          const s = statusOf(a);
          return (
            <li key={a.id}>
              <Link href={`/assets/${a.id}`} className={`card flex gap-3 border-l-8 ${STATUS[s].border} active:scale-[0.99] transition`}>
                <div className="flex-1 min-w-0">
                  <div className="flex items-start justify-between gap-2">
                    <p className="text-lg font-bold leading-tight">{a.kit_id ? "↳ " : ""}{a.name}</p>
                    <span className={`badge text-white ${STATUS[s].bg}`}>{STATUS[s].label}</span>
                  </div>
                  <p className="text-gray-600 text-sm mt-1">{a.kind.replace("_", " ")}{a.barcode ? ` · ${a.barcode}` : " · no label"}{a.serial ? ` · SN ${a.serial}` : ""}</p>
                  <p className="text-gray-700 mt-1">{a.holder?.name ? `👤 ${a.holder.name}` : a.location?.name ? `📦 ${a.location.name}` : "—"}{a.due_at && a.status === "checked_out" ? ` · due ${new Date(a.due_at).toLocaleDateString()}` : ""}</p>
                </div>
                {a.photo_url
                  ? <Image src={a.photo_url} alt="" width={80} height={80} className="w-20 h-20 rounded-xl object-cover bg-gray-100 shrink-0" />
                  : <div className="w-20 h-20 rounded-xl bg-gray-100 shrink-0 flex items-center justify-center text-3xl">🔧</div>}
              </Link>
            </li>
          );
        })}
        {shown.length === 0 && <li className="text-gray-500">Nothing matches.</li>}
      </ul>
    </>
  );
}
