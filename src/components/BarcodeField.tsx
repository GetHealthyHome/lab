"use client";
import { useState } from "react";
import Scanner from "@/components/Scanner";

// Barcode input that can be filled by pointing the camera at a pre-printed label.
export default function BarcodeField({ name = "barcode", defaultValue = "" }: { name?: string; defaultValue?: string }) {
  const [value, setValue] = useState(defaultValue);
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-col gap-2 md:col-span-2">
      <div className="flex gap-2">
        <input className="input" name={name} placeholder="Barcode / QR value" value={value} onChange={(e) => setValue(e.target.value)} />
        <button type="button" className="btn btn-secondary min-h-0 px-4 text-base whitespace-nowrap" onClick={() => setOpen((o) => !o)}>{open ? "Close" : "📷 Scan label"}</button>
      </div>
      {open && <Scanner onCode={(c) => { setValue(c); setOpen(false); }} />}
    </div>
  );
}
