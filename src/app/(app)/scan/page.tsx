import ScanFlow, { type Mode } from "@/components/ScanFlow";

export default async function ScanPage({ searchParams }: PageProps<"/scan">) {
  const { mode, code } = await searchParams;
  const m = (["checkout", "checkin", "lookup", "audit", "damage"].includes(String(mode)) ? mode : "lookup") as Mode;
  return <ScanFlow mode={m} initialCode={typeof code === "string" ? code : undefined} />;
}
