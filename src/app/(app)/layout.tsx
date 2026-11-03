import Link from "next/link";
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) redirect("/login");
  return (
    <>
      <header className="flex items-center justify-between px-4 py-3 bg-green-800 text-white">
        <Link href="/" className="text-xl font-black">🔧 Tools</Link>
        <nav className="flex gap-4 text-lg">
          <Link href="/admin">Admin</Link>
          <form action="/auth/signout" method="post"><button className="text-green-200">Sign out</button></form>
        </nav>
      </header>
      <main className="flex-1 flex flex-col gap-4 p-4 max-w-3xl mx-auto w-full">{children}</main>
    </>
  );
}
