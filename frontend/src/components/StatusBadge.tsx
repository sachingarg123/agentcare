export function StatusBadge({ status }: { status?: string | null }) {
  const s = (status || "").toUpperCase();
  let cls = "badge";
  if (
    s.includes("COMPLETE") ||
    s === "APPROVED" ||
    s === "AVAILABLE" ||
    s === "BOOKED" ||
    s === "ACTIVE" ||
    s === "SENT"
  )
    cls += " ok";
  else if (s.includes("WAIT") || s.includes("PENDING") || s.includes("RUNNING") || s === "INACTIVE")
    cls += " warn";
  else if (s.includes("BLOCK") || s.includes("FAIL") || s.includes("REJECT") || s === "CANCELLED")
    cls += " danger";
  return <span className={cls}>{status || "—"}</span>;
}
