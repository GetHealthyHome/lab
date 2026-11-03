"use client";
import { useEffect, useRef, useState } from "react";
import { BrowserMultiFormatReader, type IScannerControls } from "@zxing/browser";

// Camera viewfinder that reads QR, Code 128, Data Matrix, UPC/EAN. Typed entry is the fallback.
export default function Scanner({ onCode, paused }: { onCode: (code: string) => void; paused?: boolean }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const last = useRef<{ code: string; at: number }>({ code: "", at: 0 });

  useEffect(() => {
    if (paused || !videoRef.current) return;
    let controls: IScannerControls | undefined;
    const reader = new BrowserMultiFormatReader();
    reader
      .decodeFromConstraints({ video: { facingMode: "environment" } }, videoRef.current, (result) => {
        if (!result) return;
        const code = result.getText().trim();
        const now = Date.now();
        if (code === last.current.code && now - last.current.at < 2500) return; // ignore re-reads of the same label
        last.current = { code, at: now };
        if (navigator.vibrate) navigator.vibrate(60);
        onCode(code);
      })
      .then((c) => (controls = c))
      .catch((e) => setError(e?.message ?? "Camera unavailable"));
    return () => controls?.stop();
  }, [onCode, paused]);

  return (
    <div className="flex flex-col gap-3">
      <div className="relative rounded-2xl overflow-hidden bg-black aspect-[4/3]">
        <video ref={videoRef} className="w-full h-full object-cover" muted playsInline />
        <div className="absolute inset-6 border-4 border-orange-500/80 rounded-xl pointer-events-none" />
      </div>
      {error && <p className="text-red-600">{error}. Type the code instead.</p>}
      <form
        className="flex gap-2"
        onSubmit={(e) => { e.preventDefault(); if (typed.trim()) { onCode(typed.trim()); setTyped(""); } }}
      >
        <input className="input" placeholder="Or type a code" value={typed} onChange={(e) => setTyped(e.target.value)} />
        <button className="btn btn-secondary" type="submit">Go</button>
      </form>
    </div>
  );
}
