export const enc = encodeURIComponent;

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function fmtQty(q: string | number | null | undefined): string {
  if (q === null || q === undefined || q === "") return "—";
  const n = Number(q);
  if (Number.isNaN(n)) return String(q);
  return n.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

export function fmtScore(s: number | null | undefined): string {
  return s === null || s === undefined ? "—" : s.toFixed(2);
}

export function shortSha(s: string | null | undefined, n = 12): string {
  return s ? s.slice(0, n) : "—";
}

/** A reviewer's email as a name to show: dharma.reddy@bd.com → "Dharma Reddy". Decisions, exports and the
 *  audit trail always carry the address itself; this is for headers and labels only. */
export function displayName(email: string): string {
  const words = (email.split("@")[0] ?? "").split(/[._]+/).filter(Boolean);
  if (!words.length) return email;
  return words.map((p) => p[0].toUpperCase() + p.slice(1).toLowerCase()).join(" ");
}

/** Product family = leading digits of the SKU (e.g. 1295108NS → 1295108). Mirrors the engine's grouping. */
export function familyOf(sku: string): string {
  return sku.match(/^\d+/)?.[0] ?? sku;
}

export function fmtMoney(n: number): string {
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

export function pct(n: number, d: number): number {
  return d ? Math.round((100 * n) / d) : 0;
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function isPdf(fileName: string): boolean {
  return /\.pdf$/i.test(fileName);
}
