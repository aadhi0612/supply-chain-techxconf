// SVG helper on top of the console's h(): svg elements need createElementNS, but the same "text nodes only" rule applies.
export { h, clear, fmt, badge, stateBadge, bar, legend, kpi, table, weekDate, toast } from "../../console/js/util.js";
export const svg = (tag, attrs, ...kids) => {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs ?? {})) if (v != null && v !== false) el.setAttribute(k, v);
  for (const k of kids.flat()) if (k != null) el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return el;
};
export const short = (hash) => hash.slice(0, 8) + "…" + hash.slice(-4);
