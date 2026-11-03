"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import { createClient } from "@/lib/supabase/client";

// Shown on first sign-in (or whenever an admin resets someone) until a new password is chosen.
export default function SetPasswordPage() {
  const router = useRouter();
  const [pw, setPw] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (pw.length < 8) return setError("Use at least 8 characters.");
    if (pw !== again) return setError("Passwords do not match.");
    setBusy(true); setError(null);
    const { error } = await createClient().auth.updateUser({ password: pw, data: { must_change_password: false } });
    setBusy(false);
    if (error) return setError(error.message);
    router.replace("/");
    router.refresh();
  }

  return (
    <main className="flex-1 flex flex-col justify-center gap-6 p-6 max-w-md mx-auto w-full">
      <Image src="/logo.png" alt="LAB toolbox" width={120} height={144} className="mx-auto" />
      <h1 className="text-3xl font-black text-green-800">Choose your password</h1>
      <p className="text-gray-700 text-lg">You signed in with a temporary password. Pick a new one to continue.</p>
      <form onSubmit={save} className="flex flex-col gap-4">
        <input className="input" type="password" required autoComplete="new-password" placeholder="New password (8+ characters)" value={pw} onChange={(e) => setPw(e.target.value)} />
        <input className="input" type="password" required autoComplete="new-password" placeholder="Type it again" value={again} onChange={(e) => setAgain(e.target.value)} />
        <button className="btn btn-primary" type="submit" disabled={busy}>Save password</button>
      </form>
      {error && <p className="text-lg text-red-600">{error}</p>}
    </main>
  );
}
