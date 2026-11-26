import { lookup, suggestCrops } from "./lookup.js";
import { bindSuggest, esc, judgmentClass, loadJson, renderBanner } from "./chrome.js";

const form = document.querySelector("#batch");
const cropInput = document.querySelector("#crop");
const tableBody = document.querySelector("#rows");
const summary = document.querySelector("#summary");
let db = null;
let cropPick = null;
let lastRows = [];

function parseCsv(text) {
  const source = text.replace(/^\uFEFF/, "").trim();
  if (!source) return [];
  const lines = source.split(/\r?\n/).filter((line) => line.trim());
  const delim = lines[0].includes("\t") ? "\t" : lines[0].includes(";") && !lines[0].includes(",") ? ";" : ",";
  const split = (line) => {
    const cells = [];
    let current = "";
    let quoted = false;
    for (let i = 0; i < line.length; i += 1) {
      const ch = line[i];
      if (ch === '"') {
        if (quoted && line[i + 1] === '"') {
          current += '"';
          i += 1;
        } else quoted = !quoted;
      } else if (ch === delim && !quoted) {
        cells.push(current.trim());
        current = "";
      } else current += ch;
    }
    cells.push(current.trim());
    return cells;
  };
  const header = split(lines[0]);
  const nameAt = header.findIndex((cell) => /農殘|農藥|名稱|英文|pesticide|name/i.test(cell));
  const valueAt = header.findIndex((cell) => /檢出|含量|結果|ppm|value|殘留/i.test(cell));
  const hasHeader = nameAt >= 0;
  const body = hasHeader ? lines.slice(1) : lines;
  const nameIndex = hasHeader ? nameAt : 0;
  const valueIndex = hasHeader ? (valueAt >= 0 ? valueAt : 1) : 1;
  return body
    .map(split)
    .filter((cells) => cells.some(Boolean))
    .map((cells) => ({ name: cells[nameIndex] || "", value: cells[valueIndex] || "" }));
}

function run(entries) {
  const matrix = form.querySelector('input[name="matrix"]:checked')?.value || "fresh";
  lastRows = entries.map((entry) => {
    const found = lookup(db, {
      crop_id: cropPick?.id,
      crop: cropPick ? undefined : cropInput.value.trim(),
      pesticide: entry.name,
      value: entry.value,
      form: matrix,
    });
    return { entry, found };
  });
  const counts = { 合格: 0, 超標: 0, 其他: 0 };
  tableBody.innerHTML = lastRows
    .map(({ entry, found }) => {
      const judgment = found.judgment || (found.needs ? "待選" : "—");
      if (judgment === "合格" || judgment === "未檢出" || judgment === "免訂") counts.合格 += 1;
      else if (judgment === "超標") counts.超標 += 1;
      else counts.其他 += 1;
      return `<tr>
        <td>${esc(entry.name)}</td>
        <td>${esc(found.pesticide?.zh || "")}</td>
        <td>${esc(found.pesticide?.en || "")}</td>
        <td>${esc(entry.value)}</td>
        <td>${esc(found.limit_raw || "—")}</td>
        <td><span class="tag ${judgmentClass(judgment)}">${esc(judgment)}</span></td>
        <td>${esc(found.basis || "")}</td>
      </tr>`;
    })
    .join("");
  summary.textContent = `共 ${lastRows.length} 列。合格／未檢出／免訂 ${counts.合格}，超標 ${counts.超標}，待確認或其他 ${counts.其他}。`;
}

document.querySelector("#file").addEventListener("change", (event) => {
  const name = event.target.files[0]?.name;
  document.querySelector("#filename").textContent = name ? `已選 ${name}` : "尚未選擇檔案；沒有檔案時使用下方貼上的內容";
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!db) return;
  if (!cropPick && !cropInput.value.trim()) {
    summary.textContent = "請先選定一種原料。";
    return;
  }
  const probe = lookup(db, { crop_id: cropPick?.id, crop: cropInput.value.trim(), pesticide: "貝芬替" });
  if (probe.needs === "crop") {
    summary.textContent = probe.basis;
    return;
  }
  const file = document.querySelector("#file").files[0];
  const text = file ? await file.text() : document.querySelector("#paste").value;
  const entries = parseCsv(text);
  if (!entries.length) {
    summary.textContent = "沒有讀到資料。請貼上「農殘名稱,檢出量」。";
    return;
  }
  run(entries);
});

document.querySelector("#export").addEventListener("click", () => {
  if (!lastRows.length) return;
  const header = ["原料", "型態", "輸入名稱", "中文名", "英文名", "檢出量", "容許量", "判定", "適用依據", "作物類別"];
  const matrix = form.querySelector('input[name="matrix"]:checked')?.value === "dried" ? "乾燥" : "新鮮";
  const lines = [header.join(",")];
  for (const { entry, found } of lastRows) {
    const cells = [
      cropInput.value.trim(),
      matrix,
      entry.name,
      found.pesticide?.zh || "",
      found.pesticide?.en || "",
      entry.value,
      found.limit_raw || "",
      found.judgment || "",
      found.basis || "",
      found.crop?.category || "",
    ].map((cell) => `"${String(cell).replaceAll('"', '""')}"`);
    lines.push(cells.join(","));
  }
  const blob = new Blob(["\uFEFF" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${cropInput.value.trim() || "mrl"}-判定.csv`;
  link.click();
  URL.revokeObjectURL(url);
});

bindSuggest(
  cropInput,
  document.querySelector("#crop-menu"),
  (query) =>
    db
      ? suggestCrops(db.crops, db.aliases, query, 8).map((crop) => ({
          id: crop.id,
          label: crop.name,
          hint: crop.category,
        }))
      : [],
  (item) => {
    cropPick = item;
  },
);

summary.textContent = "正在載入容許量表…";
try {
  const [meta, database] = await Promise.all([loadJson("data/meta.json"), loadJson("data/db.json")]);
  db = database;
  renderBanner(meta);
  summary.textContent = "先選原料，再貼上或上傳篩檢表。第一列可以是標題。";
} catch (error) {
  summary.textContent = `資料讀取失敗：${error.message}`;
}
