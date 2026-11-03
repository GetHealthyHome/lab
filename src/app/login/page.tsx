"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function signIn(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    const { data, error } = await createClient().auth.signInWithPassword({ email, password });
    setBusy(false);
    if (error) return setError("Email or password did not match. Ask your admin if you need a reset.");
    router.replace(data.user.user_metadata?.must_change_password ? "/set-password" : "/");
    router.refresh();
  }

  return (
    <main className="flex-1 flex flex-col justify-center gap-6 p-6 max-w-md mx-auto w-full">
      <h1 className="text-3xl font-black text-green-800">Lab Tool Tracker</h1>
      <form onSubmit={signIn} className="flex flex-col gap-4">
        <input className="input" type="email" required autoComplete="username" placeholder="you@school.edu" value={email} onChange={(e) => setEmail(e.target.value)} />
        <input className="input" type="password" required autoComplete="current-password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} />
        <button className="btn btn-primary" type="submit" disabled={busy}>Sign in</button>
      </form>
      {error && <p className="text-lg text-red-600">{error}</p>}
      <p className="text-gray-600">Accounts are created by the lab admin. First sign-in uses the temporary password you were given.</p>
    </main>
  );
}
