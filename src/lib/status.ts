// One place for "what colour is this tool right now": green available, orange out, red overdue, blue repair/lost.
export type StatusKey = "available" | "out" | "overdue" | "repair";

export function statusOf(a: { status: string; due_at: string | null }): StatusKey {
  if (a.status === "checked_out") return a.due_at && new Date(a.due_at).getTime() < Date.now() ? "overdue" : "out";
  if (a.status === "available") return "available";
  return "repair";
}

export const STATUS = {
  available: { label: "Available", bg: "bg-green-600", border: "border-l-green-600", text: "text-green-700" },
  out: { label: "Checked out", bg: "bg-orange-500", border: "border-l-orange-500", text: "text-orange-600" },
  overdue: { label: "Overdue", bg: "bg-red-600", border: "border-l-red-600", text: "text-red-600" },
  repair: { label: "Repair / lost", bg: "bg-blue-600", border: "border-l-blue-600", text: "text-blue-700" },
} as const;
