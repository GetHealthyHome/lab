import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { BottomNav, Logo, SideNav } from "@/components/Nav";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) redirect("/login");
  const { data: me } = await supabase.from("people").select("name").eq("auth_user_id", user.id).maybeSingle();
  const userName = me?.name ?? user.email ?? "";
  return (
    <div className="flex min-h-screen">
      <SideNav userName={userName} />
      <div className="flex-1 flex flex-col min-w-0">
        <header className="md:hidden flex items-center justify-between px-4 py-2 bg-white border-b border-gray-200">
          <Logo size={44} />
          <form action="/auth/signout" method="post"><button className="text-sm text-gray-500">Sign out</button></form>
        </header>
        <main className="flex-1 flex flex-col gap-4 p-4 pb-28 md:pb-8 max-w-5xl w-full mx-auto">{children}</main>
        <BottomNav />
      </div>
    </div>
  );
}
