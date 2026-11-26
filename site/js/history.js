import { esc, formatTaipei, loadJson, renderBanner } from "./chrome.js";

const body = document.querySelector("#events");
const count = document.querySelector("#count");
const query = document.querySelector("#q");
const kind = document.querySelector("#kind");
let events = [];

function paint() {
  const text = query.value.trim().toLowerCase();
  const change = kind.value;
  const shown = events.filter((event) => {
    if (change && event.change !== change) return false;
    if (!text) return true;
    const blob = `${event.pesticide_zh} ${event.pesticide_en} ${event.crop} ${event.field} ${event.old} ${event.new}`.toLowerCase();
    return blob.includes(text);
  });
  count.textContent = `顯示 ${shown.length} / ${events.length} 筆`;
  body.innerHTML = shown.length
    ? shown
        .map(
          (event) => `<tr>
            <td>${esc(event.date)}</td>
            <td>${esc(event.change)}</td>
            <td>${esc(event.pesticide_zh)} ${esc(event.pesticide_en)}</td>
            <td>${esc(event.crop)}</td>
            <td>${esc(event.field)}<br>${esc(event.old)} → ${esc(event.new)}</td>
            <td>${esc(event.annex)}${event.source ? `<br><a href="${esc(event.source)}">來源</a>` : ""}</td>
          </tr>`,
        )
        .join("")
    : `<tr><td colspan="6">沒有符合的紀錄。初始快照不會把既有列記成新增。</td></tr>`;
}

query.addEventListener("input", paint);
kind.addEventListener("change", paint);

try {
  const [meta, past, runs] = await Promise.all([
    loadJson("data/meta.json"),
    loadJson("data/history.json"),
    loadJson("data/sync-log.json"),
  ]);
  events = past.slice().reverse();
  renderBanner(meta);
  const latest = runs[runs.length - 1];
  document.querySelector("#latest").textContent = latest
    ? `最近一次同步 ${formatTaipei(latest.ran_at)}，異動 ${latest.diff_total || 0} 筆。${latest.note || ""}`
    : "尚無同步紀錄。";
  paint();
} catch (error) {
  count.textContent = `讀取失敗：${error.message}`;
}
