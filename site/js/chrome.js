export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

export function formatTaipei(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat("zh-Hant-TW", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date);
}

export async function loadJson(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path} ${response.status}`);
  return response.json();
}

export function renderBanner(meta) {
  const banner = document.querySelector("#banner");
  if (!banner || !meta) return;
  const counts = meta.counts || {};
  banner.innerHTML = `<div class="wrap">
    <span>資料時間 <strong>${esc(formatTaipei(meta.synced_at))}</strong></span>
    <span>法規修正 <strong>${esc(meta.law_amended_on || "未能取得")}</strong></span>
    <span>附表一 ${esc(counts["附表一"] ?? "—")} 列</span>
    <span>附表四禁用 ${esc(counts["附表四"] ?? "—")}</span>
    <span>附表五 ${esc(counts["附表五類別"] ?? "—")} 類</span>
  </div>`;
  const host = document.querySelector("#alerts");
  if (!host) return;
  const mismatches = meta.crosscheck_mismatches || [];
  const warnings = (meta.warnings || []).filter((item) => !mismatches.includes(item));
  host.innerHTML = `
    ${mismatches.length ? `<div class="alert"><strong>交叉比對不一致，未隱藏</strong><ul>${mismatches.map((item) => `<li>${esc(item)}</li>`).join("")}</ul></div>` : ""}
    ${warnings.length ? `<details class="quality" open><summary>資料清理與下載備註（${warnings.length}）</summary><ul>${warnings.map((item) => `<li>${esc(item)}</li>`).join("")}</ul></details>` : ""}
  `;
}

export function bindSuggest(input, menu, fetchItems, onPick) {
  let items = [];
  let active = -1;

  function paint() {
    if (!items.length) {
      menu.hidden = true;
      menu.innerHTML = "";
      return;
    }
    menu.hidden = false;
    menu.innerHTML = items
      .map(
        (item, index) =>
          `<button type="button" class="opt${index === active ? " is-active" : ""}" data-i="${index}"><span>${esc(item.label)}</span><small>${esc(item.hint || "")}</small></button>`,
      )
      .join("");
  }

  function choose(item) {
    if (!item) return;
    input.value = item.label;
    menu.hidden = true;
    onPick(item);
  }

  input.addEventListener("input", () => {
    onPick(null);
    items = fetchItems(input.value.trim()).slice(0, 8);
    active = -1;
    paint();
  });
  input.addEventListener("keydown", (event) => {
    if (menu.hidden || !items.length) return;
    if (event.key === "ArrowDown") {
      active = Math.min(items.length - 1, active + 1);
      paint();
      event.preventDefault();
    } else if (event.key === "ArrowUp") {
      active = Math.max(0, active - 1);
      paint();
      event.preventDefault();
    } else if (event.key === "Enter" && active >= 0) {
      choose(items[active]);
      event.preventDefault();
    } else if (event.key === "Escape") {
      menu.hidden = true;
    }
  });
  menu.addEventListener("mousedown", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    event.preventDefault();
    choose(items[Number(button.dataset.i)]);
  });
  input.addEventListener("blur", () => {
    setTimeout(() => {
      menu.hidden = true;
    }, 150);
  });
}

export function judgmentClass(judgment) {
  if (["合格", "未檢出", "免訂"].includes(judgment)) return "pass";
  if (["超標"].includes(judgment) || (judgment || "").includes("禁用")) return "fail";
  return "warn";
}
