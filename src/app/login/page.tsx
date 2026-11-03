"use client";
import { useState } from "react";
import { createClient } from "@/lib/supabase/client";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const { error } = await createClient().auth.signInWithOtp({
      email,
      options: { emailRedirectTo: `${location.origin}/auth/callback` },
    });
    setMsg(error ? error.message : "Check your email for a sign-in link.");
  }

  return (
    <main className="flex-1 flex flex-col justify-center gap-6 p-6 max-w-md mx-auto w-full">
      <h1 className="text-3xl font-black">Lab Tool Tracker</h1>
      <form onSubmit={send} className="flex flex-col gap-4">
        <input className="input" type="email" required placeholder="you@school.edu" value={email} onChange={(e) => setEmail(e.target.value)} />
        <button className="btn btn-primary" type="submit">Email me a sign-in link</button>
      </form>
      {msg && <p className="text-lg">{msg}</p>}
    </main>
  );
}
