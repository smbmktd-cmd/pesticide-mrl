import { lookup, suggestCrops, suggestPesticides } from "./lookup.js";
import { bindSuggest, esc, judgmentClass, loadJson, renderBanner } from "./chrome.js";

const resultEl = document.querySelector("#result");
const form = document.querySelector("#query");
const cropInput = document.querySelector("#crop");
const pestInput = document.querySelector("#pest");
const valueInput = document.querySelector("#value");
let db = null;
let history = [];
let cropPick = null;
let pestPick = null;

function formValue() {
  return form.querySelector('input[name="matrix"]:checked')?.value || "fresh";
}

function setStatus(html) {
  resultEl.innerHTML = `<p class="loading">${html}</p>`;
}

function historyFor(found) {
  const keys = new Set(found.history_keys || []);
  return history.filter(
    (event) =>
      (event.history_key && keys.has(event.history_key)) ||
      (found.crop && event.change === "分類變更" && event.crop === found.crop.name),
  );
}

function render(found) {
  if (found.needs === "crop") {
    const buttons = (found.crop_candidates || [])
      .map(
        (crop) =>
          `<button type="button" class="chip" data-crop-id="${esc(crop.id)}" data-crop-name="${esc(crop.name)}">${esc(crop.name)} · ${esc(crop.category)}</button>`,
      )
      .join("");
    resultEl.innerHTML = `<article class="panel result">
      <p class="basis"><span class="k">適用依據</span>${esc(found.basis)}</p>
      <div class="candidates">${buttons || "<span>沒有相近作物。</span>"}</div>
    </article>`;
    return;
  }
  if (found.needs === "pesticide") {
    const buttons = (found.pesticide_candidates || [])
      .map(
        (pest) =>
          `<button type="button" class="chip" data-pest-id="${esc(pest.id)}" data-pest-name="${esc(pest.zh || pest.en)}">${esc(pest.zh || pest.en)} <small>${esc(pest.en)}</small></button>`,
      )
      .join("");
    resultEl.innerHTML = `<article class="panel result">
      <p class="basis"><span class="k">適用依據</span>${esc(found.basis)}</p>
      <div class="candidates">${buttons}</div>
    </article>`;
    return;
  }

  const klass = judgmentClass(found.judgment);
  const rows = (found.rows || [])
    .map(
      (row) => `<tr>
        <td>${esc(row.annex)}</td>
        <td>${esc(row.crop_raw)}</td>
        <td>${esc(row.limit_raw)} ppm</td>
        <td>${esc(row.note_raw)}${row.note !== row.note_raw ? ` → ${esc(row.note)}` : ""}</td>
        <td>${esc(row.zh_raw || row.zh)} ${esc(row.en)}</td>
      </tr>`,
    )
    .join("");
  const notes = (found.footnotes || [])
    .map((note) => `<li><strong>${esc(note.id)}</strong> ${esc(note.text)}</li>`)
    .join("");
  const events = historyFor(found);
  const eventRows = events.length
    ? events
        .map(
          (event) => `<tr>
            <td>${esc(event.date)}</td>
            <td>${esc(event.change)}</td>
            <td>${esc(event.field)}</td>
            <td>${esc(event.old)} → ${esc(event.new)}</td>
          </tr>`,
        )
        .join("")
    : `<tr><td colspan="4">此列自本站建置以來沒有異動。</td></tr>`;
  const crop = found.crop;
  resultEl.innerHTML = `<article class="panel result">
    <div class="seal-row">
      <div class="seal ${klass}"><b>${esc(found.judgment || "—")}</b><span>${esc(found.judgment_detail || "")}</span></div>
      <div>
        <p class="basis"><span class="k">適用依據</span>${esc(found.basis)}</p>
        ${found.buffer_warning ? `<p class="callout">${esc(found.buffer_warning)}<br>備註：${esc(found.rows?.[0]?.note || found.buffer?.raw || "")}</p>` : ""}
        <p class="meta-line">容許量 <strong>${found.limit_raw ? esc(found.limit_raw) + " ppm" : "—"}</strong>
          ${found.star ? "（* 為註五定量極限）" : ""}
          ${found.rows?.[0]?.note ? `／ 備註 ${esc(found.rows[0].note)}` : ""}
          ／ 檢出 ${esc(valueInput.value || "未填")}
          ／ ${formValue() === "dried" ? "乾燥" : "新鮮"}市售型態</p>
        ${found.via_note ? `<p>${esc(found.via_note)}</p>` : ""}
        ${found.alias_note ? `<p>${esc(found.alias_note)}</p>` : ""}
      </div>
    </div>
    <div class="cards">
      <section class="card">
        <h3>作物分類</h3>
        <p>${esc(crop?.name || "")} → <strong>${esc(crop?.category || "")}</strong></p>
        <p class="meta-line">附表五：${esc(crop?.excerpt || "")}</p>
        ${crop?.separation_note ? `<p>${esc(crop.separation_note)}</p>` : ""}
        ${crop?.aliases?.length ? `<p class="meta-line">未另列的包含名稱：${esc(crop.aliases.join("、"))}</p>` : ""}
      </section>
      <section class="card">
        <h3>比對順序</h3>
        <ol class="trace">${(found.trace || []).map((line) => `<li>${esc(line)}</li>`).join("")}</ol>
      </section>
    </div>
    ${found.official_text ? `<p><span class="tag">官方列示</span> ${esc(found.official_text)}</p>` : ""}
    ${rows ? `<div class="table-wrap"><table>
      <thead><tr><th>表</th><th>作物類別（原始）</th><th>容許量</th><th>備註</th><th>農藥</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>` : ""}
    ${notes ? `<h3>相關註</h3><ul class="note-list">${notes}</ul>` : ""}
    <h3>這一列的更新歷程</h3>
    <div class="table-wrap"><table>
      <thead><tr><th>日期</th><th>類型</th><th>欄位</th><th>舊 → 新</th></tr></thead>
      <tbody>${eventRows}</tbody>
    </table></div>
  </article>`;
}

function run() {
  if (!db) return;
  const found = lookup(db, {
    crop: cropPick ? undefined : cropInput.value.trim(),
    crop_id: cropPick?.id,
    pesticide: pestPick ? undefined : pestInput.value.trim(),
    pesticide_id: pestPick?.id,
    value: valueInput.value.trim(),
    form: formValue(),
  });
  render(found);
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  run();
});
resultEl.addEventListener("click", (event) => {
  const cropBtn = event.target.closest("[data-crop-id]");
  if (cropBtn) {
    cropPick = { id: cropBtn.dataset.cropId, label: cropBtn.dataset.cropName };
    cropInput.value = cropBtn.dataset.cropName;
    run();
    return;
  }
  const pestBtn = event.target.closest("[data-pest-id]");
  if (pestBtn) {
    pestPick = { id: pestBtn.dataset.pestId, label: pestBtn.dataset.pestName };
    pestInput.value = pestBtn.dataset.pestName;
    run();
  }
});
document.querySelectorAll(".chip[data-demo]").forEach((button) => {
  button.addEventListener("click", () => {
    cropPick = null;
    pestPick = null;
    cropInput.value = button.dataset.crop;
    pestInput.value = button.dataset.pest;
    valueInput.value = button.dataset.value || "";
    form.querySelector(`input[name="matrix"][value="${button.dataset.form || "fresh"}"]`).checked = true;
    run();
  });
});

bindSuggest(
  cropInput,
  document.querySelector("#crop-menu"),
  (query) =>
    db
      ? suggestCrops(db.crops, db.aliases, query, 8).map((crop) => ({
          id: crop.id,
          label: crop.name,
          hint: `${crop.category}${crop.separated_from ? ` · 與${crop.separated_from}分開列` : ""}`,
        }))
      : [],
  (item) => {
    cropPick = item;
  },
);
bindSuggest(
  pestInput,
  document.querySelector("#pest-menu"),
  (query) =>
    db
      ? suggestPesticides(db.pesticides, query, 8).map((pest) => ({
          id: pest.id,
          label: pest.zh || pest.en,
          hint: [pest.en, pest.exempt ? "免訂" : "", pest.banned ? "禁用" : ""].filter(Boolean).join(" · "),
        }))
      : [],
  (item) => {
    pestPick = item;
  },
);

setStatus("正在載入容許量表…");
try {
  const [meta, database, past] = await Promise.all([
    loadJson("data/meta.json"),
    loadJson("data/db.json"),
    loadJson("data/history.json").catch(() => []),
  ]);
  db = database;
  history = past;
  renderBanner(meta);
  setStatus("輸入原料與農殘後按查詢。附表五名單以「等」結尾，對到多筆時會請你選，不會代猜。");
} catch (error) {
  setStatus(`資料讀取失敗：${esc(error.message)}。請用網站伺服器開啟（GitHub Pages 或 <code>python3 -m http.server</code>），不要直接點開檔案。`);
}
