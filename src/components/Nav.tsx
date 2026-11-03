"use client";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";

const ICONS = {
  home: "M2.25 12l8.954-8.955c.44-.439 1.152-.439 1.591 0L21.75 12M4.5 9.75v10.125c0 .621.504 1.125 1.125 1.125H9.75v-4.875c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21h4.125c.621 0 1.125-.504 1.125-1.125V9.75M8.25 21h8.25",
  items: "M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z",
  scan: "M3.75 4.875c0-.621.504-1.125 1.125-1.125h4.5c.621 0 1.125.504 1.125 1.125v4.5c0 .621-.504 1.125-1.125 1.125h-4.5A1.125 1.125 0 013.75 9.375v-4.5zM3.75 14.625c0-.621.504-1.125 1.125-1.125h4.5c.621 0 1.125.504 1.125 1.125v4.5c0 .621-.504 1.125-1.125 1.125h-4.5a1.125 1.125 0 01-1.125-1.125v-4.5zM13.5 4.875c0-.621.504-1.125 1.125-1.125h4.5c.621 0 1.125.504 1.125 1.125v4.5c0 .621-.504 1.125-1.125 1.125h-4.5A1.125 1.125 0 0113.5 9.375v-4.5zM6.75 6.75h.75v.75h-.75v-.75zM6.75 16.5h.75v.75h-.75v-.75zM16.5 6.75h.75v.75h-.75v-.75zM13.5 13.5h.75v.75h-.75v-.75zM13.5 19.5h.75v.75h-.75v-.75zM19.5 13.5h.75v.75h-.75v-.75zM19.5 19.5h.75v.75h-.75v-.75zM16.5 16.5h.75v.75h-.75v-.75z",
  admin: "M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.324.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.24-.438.613-.431.992a6.759 6.759 0 010 .255c-.007.378.138.75.43.99l1.005.828c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.57 6.57 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.02-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.932 0 010-.255c.007-.378-.138-.75-.43-.99l-1.004-.828a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.087.22-.128.332-.183.582-.495.644-.869l.214-1.281zM15 12a3 3 0 11-6 0 3 3 0 016 0z",
};

export function Icon({ name, className = "w-6 h-6" }: { name: keyof typeof ICONS; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      <path d={ICONS[name]} />
    </svg>
  );
}

const LINKS = [
  { href: "/", label: "Home", icon: "home" },
  { href: "/items", label: "Items", icon: "items" },
  { href: "/scan?mode=lookup", label: "Scan", icon: "scan" },
  { href: "/admin", label: "Admin", icon: "admin" },
] as const;

const isActive = (path: string, href: string) => (href === "/" ? path === "/" : path.startsWith(href.split("?")[0]));

export function Logo({ size = 72 }: { size?: number }) {
  return <Image src="/logo.png" alt="LAB toolbox" width={size} height={Math.round(size * 1.2)} priority />;
}

export function SideNav({ userName }: { userName: string }) {
  const path = usePathname();
  return (
    <aside className="hidden md:flex flex-col w-60 shrink-0 bg-green-900 text-white min-h-screen sticky top-0">
      <div className="bg-white p-4 flex justify-center"><Logo size={96} /></div>
      <p className="px-5 pt-4 pb-2 text-green-200 text-sm">Signed in as <span className="text-white font-bold">{userName}</span></p>
      <nav className="flex flex-col gap-1 p-3 flex-1">
        {LINKS.map((l) => (
          <Link key={l.href} href={l.href} className={`flex items-center gap-3 rounded-xl px-4 py-3 text-lg font-semibold ${isActive(path, l.href) ? "bg-orange-500 text-white" : "text-green-100 hover:bg-green-800"}`}>
            <Icon name={l.icon} /> {l.label}
          </Link>
        ))}
      </nav>
      <form action="/auth/signout" method="post" className="p-3 border-t border-green-800">
        <button className="w-full text-left rounded-xl px-4 py-3 text-green-200 hover:bg-green-800">Sign out</button>
      </form>
    </aside>
  );
}

export function BottomNav() {
  const path = usePathname();
  return (
    <nav className="md:hidden fixed bottom-0 inset-x-0 bg-green-900 text-white border-t border-green-800 grid grid-cols-4 pb-[env(safe-area-inset-bottom)] z-20">
      {LINKS.map((l) => (
        <Link key={l.href} href={l.href} className={`flex flex-col items-center gap-0.5 py-2 text-xs font-bold ${isActive(path, l.href) ? "text-orange-400" : "text-green-100"}`}>
          {l.icon === "scan" ? (
            <span className="-mt-6 rounded-full bg-orange-500 text-white p-3 shadow-lg"><Icon name="scan" className="w-7 h-7" /></span>
          ) : <Icon name={l.icon} className="w-7 h-7" />}
          {l.label}
        </Link>
      ))}
    </nav>
  );
}
