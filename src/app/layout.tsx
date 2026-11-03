import type { Metadata, Viewport } from "next";
import { Geist } from "next/font/google";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Lab Tool Tracker",
  description: "Scan, check out, and find every tool in the hands-on lab.",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, statusBarStyle: "default", title: "Tools" },
  icons: { apple: "/icons/icon-192.png" },
};

export const viewport: Viewport = { themeColor: "#166534", width: "device-width", initialScale: 1, maximumScale: 1 };

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">
        {children}
        <script dangerouslySetInnerHTML={{ __html: `if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js')` }} />
      </body>
    </html>
  );
}
