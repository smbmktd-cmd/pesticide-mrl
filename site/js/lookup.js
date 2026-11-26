/**
 * 容許量查詢。優先順序與 pipeline/lookup.py 相同，給靜態網站與 node 測試共用。
 * 1 免訂 → 2 個別列 → 3 其他類(除外) → 4 子類／類別 → 5 註五 → 6 禁用 → 7 第3條不得檢出
 */

const CATEGORY_ALIASES = {
  米類: ["米"],
  茶類: ["茶"],
};

const GRAIN = new Set(["米類", "麥類", "雜糧類", "乾豆類"]);
const ND = new Set(["nd", "n.d", "n.d.", "未檢出", "陰性", "nd.", "—", "-", "－", "<loq", "＜loq", "trace", "traces"]);
const BUFFER = /於\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日刪除，緩衝期\s*(\d+)\s*年/;

function bufferInfo(note) {
  const text = note || "";
  if (!text.includes("刪除")) return null;
  const info = { deleted: true, raw: text, end: null };
  const matched = text.match(BUFFER);
  if (!matched) return info;
  const year = Number(matched[1]) + 1911 + Number(matched[4]);
  const month = String(matched[2]).padStart(2, "0");
  const day = String(matched[3]).padStart(2, "0");
  info.end = `${year}-${month}-${day}`;
  return info;
}

function bufferWarning(info) {
  if (!info) return "";
  let text = "此標準已刪除，緩衝期內適用，請確認產品製造/輸入日期";
  if (info.end) text += `（緩衝期至 ${info.end}）`;
  return text;
}

function clean(value) {
  return String(value ?? "").replace(/\u3000/g, " ").replace(/\s+/g, " ").trim();
}

export function normZhKey(value) {
  return clean(value)
    .replace(/\s+/g, "")
    .replace(/[‧·•・．]/g, "")
    .replace(/（/g, "(")
    .replace(/）/g, ")")
    .toLowerCase();
}

export function normEnKey(value) {
  return clean(value)
    .toLowerCase()
    .replace(/[‧·•・．､，]/g, ",")
    .replace(/[^0-9a-z]+/g, "");
}

function cmpDecimal(a, b) {
  const prep = (raw) => {
    const text = String(raw).trim();
    const neg = text.startsWith("-");
    const body = neg ? text.slice(1) : text;
    const [whole, frac = ""] = body.split(".");
    const fracTrim = frac.replace(/0+$/, "");
    return { neg, whole: whole.replace(/^0+(?=\d)/, "") || "0", frac: fracTrim };
  };
  const left = prep(a);
  const right = prep(b);
  const scale = Math.max(left.frac.length, right.frac.length);
  const leftInt = BigInt((left.whole + left.frac.padEnd(scale, "0")).replace(/^0+(?=\d)/, "") || "0");
  const rightInt = BigInt((right.whole + right.frac.padEnd(scale, "0")).replace(/^0+(?=\d)/, "") || "0");
  const lv = left.neg ? -leftInt : leftInt;
  const rv = right.neg ? -rightInt : rightInt;
  return lv < rv ? -1 : lv > rv ? 1 : 0;
}

function note5Base(category, form) {
  if (GRAIN.has(category)) return "穀類";
  if (category === "茶類") return "茶類";
  if (category === "香辛植物及其他草木本植物") return form === "dried" ? "茶類" : "蔬果類";
  if (form === "dried") return "茶類";
  return "蔬果類";
}

function cropNames(crop) {
  return new Set([crop.name, ...(crop.match_names || [])]);
}

function excluded(row, crop) {
  const names = cropNames(crop);
  return (row.exclusions || []).some((item) => names.has(item));
}

function specificHit(row, crop) {
  if (row.kind !== "specific" || excluded(row, crop)) return false;
  if (crop.name === row.label_core || crop.name === row.base) return true;
  if (!row.form && (crop.match_names || []).includes(row.base)) return true;
  if ((crop.match_names || []).includes(row.label_core)) return true;
  return false;
}

function categoryHit(row, crop) {
  if (excluded(row, crop)) return false;
  const aliases = CATEGORY_ALIASES[crop.category] || [];
  return row.base === crop.category || aliases.includes(row.base);
}

function formOk(row, form) {
  if (row.form === "dried" && form !== "dried") return false;
  if (row.form === "fresh" && form !== "fresh") return false;
  return true;
}

function preferForm(rows, form) {
  const typed = rows.filter((row) => row.form === form);
  if (typed.length) return typed;
  return rows.filter((row) => !row.form);
}

function narrow(rows) {
  if (!rows.length) return [];
  let bestDepth = -1;
  let bestEx = -1;
  for (const row of rows) {
    const depth = row.depth || 0;
    const ex = (row.exclusions || []).length;
    if (depth > bestDepth || (depth === bestDepth && ex > bestEx)) {
      bestDepth = depth;
      bestEx = ex;
    }
  }
  return rows.filter((row) => (row.depth || 0) === bestDepth && (row.exclusions || []).length === bestEx);
}

function limitsAgree(rows) {
  return new Set(rows.map((row) => row.limit)).size === 1;
}

function basisFor(step, row, form) {
  const label = row.crop || "";
  let text;
  if (step === "specific") text = `依${label}個別標準`;
  else if (step === "note5") text = `依${label}，註五定量極限`;
  else text = `依${label}標準`;
  if (row.annex === "附表二") text += "（附表二外源性）";
  if (form === "dried" && row.form !== "dried" && step !== "note5") {
    text += "（第2條以市售型態計，不另乘濃縮係數）";
  }
  if (step === "note5" && form === "dried" && row.base === "茶類") {
    text += "（乾燥基質對應檢驗方法 III 類）";
  }
  return text;
}

function parseAmount(value) {
  if (value == null) return { kind: "empty" };
  const text = String(value).trim();
  if (!text) return { kind: "empty" };
  const compact = text.replace(/ /g, "").replace(/ppm/gi, "");
  if (ND.has(compact.toLowerCase()) || ["未檢出", "陰性", "N.D.", "ND"].includes(compact)) {
    return { kind: "nd" };
  }
  if ("<＜≤".includes(compact[0])) {
    try {
      const num = compact.slice(1);
      if (!/^\d+(\.\d+)?$/.test(num)) return { kind: "invalid", raw: text };
      return { kind: "lt", value: num };
    } catch {
      return { kind: "invalid", raw: text };
    }
  }
  const num = compact.replace(/,/g, "");
  if (!/^\d+(\.\d+)?$/.test(num)) return { kind: "invalid", raw: text };
  return { kind: "num", value: num };
}

function judge(amount, mode, limit) {
  if (amount.kind === "invalid") return ["無法判定", "檢出量無法解讀"];
  if (amount.kind === "empty") return ["未輸入檢出量", ""];
  if (mode === "exempt") return ["免訂", "附表三所列，第4條毋需依殘留量判定"];
  if (mode === "nondetect") {
    if (amount.kind === "nd" || (amount.kind === "num" && cmpDecimal(amount.value, "0") <= 0)) {
      return ["未檢出", "殘留為未檢出"];
    }
    if (amount.kind === "lt") return ["無法判定", "報告以小於符號表示，無法確認是否未檢出"];
    return ["超標", "不得檢出"];
  }
  if (amount.kind === "nd" || (amount.kind === "num" && cmpDecimal(amount.value, "0") <= 0)) {
    return ["合格", "未檢出，未逾容許量"];
  }
  if (amount.kind === "lt") {
    if (cmpDecimal(amount.value, limit) <= 0) return ["合格", "報告值上限未逾容許量"];
    return ["無法判定", "小於符號的上限高於容許量，無法確認是否超標"];
  }
  if (cmpDecimal(amount.value, limit) <= 0) return ["合格", "檢出量未逾容許量"];
  return ["超標", "檢出量逾容許量"];
}

function publicCrop(crop) {
  return {
    id: crop.id,
    name: crop.name,
    category: crop.category,
    groups: crop.groups || [],
    excerpt: crop.excerpt || crop.name,
    separated_from: crop.separated_from,
    separation_note: crop.separation_note,
    aliases: crop.aliases || [],
  };
}

function publicPest(pest) {
  return {
    id: pest.id,
    en: pest.en || "",
    zh: pest.zh || "",
    exempt: !!pest.exempt,
    banned: !!pest.banned,
    ban_make: pest.ban_make || "",
    ban_use: pest.ban_use || "",
  };
}

function publicRow(row) {
  return {
    id: row.id,
    annex: row.annex,
    en: row.en || "",
    zh: row.zh || "",
    crop: row.crop || "",
    crop_raw: row.crop_raw || "",
    limit: row.limit || "",
    limit_raw: row.limit_raw || "",
    star: !!row.star,
    note: row.note || "",
    note_raw: row.note_raw || "",
    kind: row.kind,
    history_key: row.history_key,
    repairs: row.repairs || [],
  };
}

export function suggestCrops(crops, aliases, query, limit = 12) {
  const key = normZhKey(query);
  if (!key) return [];
  const scored = [];
  for (const crop of crops) {
    const nameKey = normZhKey(crop.name);
    let score = null;
    if (nameKey === key) score = 0;
    else if (nameKey.startsWith(key)) score = 1;
    else if (nameKey.includes(key)) score = 2;
    else {
      for (const alias of crop.aliases || []) {
        const aliasKey = normZhKey(alias);
        if (aliasKey.startsWith(key) || aliasKey.includes(key)) {
          score = 3;
          break;
        }
      }
    }
    if (score != null) scored.push([score, crop]);
  }
  for (const alias of aliases || []) {
    if (key === alias.key || alias.key.includes(key)) {
      for (const crop of crops) {
        if ((alias.crop_ids || []).includes(crop.id)) {
          scored.push([key === alias.key ? 1 : 4, crop]);
        }
      }
    }
  }
  scored.sort((a, b) => a[0] - b[0] || a[1].name.length - b[1].name.length);
  const seen = new Set();
  const out = [];
  for (const [, crop] of scored) {
    if (seen.has(crop.id)) continue;
    seen.add(crop.id);
    out.push(crop);
    if (out.length >= limit) break;
  }
  return out;
}

export function suggestPesticides(pesticides, query, limit = 12) {
  const zh = normZhKey(query);
  const en = normEnKey(query);
  const scored = [];
  for (const pest of pesticides) {
    let score = null;
    if (zh && pest.zh_key === zh) score = 0;
    else if (en && pest.en_key === en) score = 0;
    else if (zh && (pest.zh_key || "").startsWith(zh)) score = 1;
    else if (en && en.length >= 3 && (pest.en_key || "").startsWith(en)) score = 1;
    else if (zh && (pest.zh_key || "").includes(zh)) score = 2;
    else if (en && en.length >= 3 && (pest.en_key || "").includes(en)) score = 2;
    else {
      for (const alias of pest.aliases || []) {
        if (zh && normZhKey(alias.zh || "").includes(zh)) {
          score = 3;
          break;
        }
        if (en && en.length >= 3 && normEnKey(alias.en || "").includes(en)) {
          score = 3;
          break;
        }
      }
    }
    if (score != null) scored.push([score, pest]);
  }
  scored.sort((a, b) => a[0] - b[0] || String(a[1].en || a[1].zh).localeCompare(String(b[1].en || b[1].zh)));
  const seen = new Set();
  const out = [];
  for (const [, pest] of scored) {
    if (seen.has(pest.id)) continue;
    seen.add(pest.id);
    out.push(pest);
    if (out.length >= limit) break;
  }
  return out;
}

function resolveCrop(db, query, cropId) {
  const crops = db.crops || [];
  if (cropId) {
    const crop = crops.find((item) => item.id === cropId);
    return crop
      ? { crop, candidates: [], alias_note: null }
      : { crop: null, candidates: [], alias_note: null };
  }
  const key = normZhKey(query || "");
  if (!key) return { crop: null, candidates: [], alias_note: null };
  const exact = crops.filter((crop) => (crop.exact_keys || []).includes(key));
  const uniq = new Map(exact.map((crop) => [crop.id, crop]));
  if (uniq.size === 1) return { crop: [...uniq.values()][0], candidates: [], alias_note: null };
  if (uniq.size > 1) return { crop: null, candidates: [...uniq.values()], alias_note: null };
  const aliasHits = (db.aliases || []).filter((alias) => alias.key === key);
  const linked = [];
  const notes = [];
  for (const alias of aliasHits) {
    notes.push(alias.note);
    for (const crop of crops) {
      if ((alias.crop_ids || []).includes(crop.id)) linked.push(crop);
    }
  }
  const linkedUniq = new Map(linked.map((crop) => [crop.id, crop]));
  if (linkedUniq.size === 1) {
    return { crop: [...linkedUniq.values()][0], candidates: [], alias_note: notes[0] || null };
  }
  if (linkedUniq.size > 1) return { crop: null, candidates: [...linkedUniq.values()], alias_note: null };
  return { crop: null, candidates: suggestCrops(crops, db.aliases || [], query || ""), alias_note: null };
}

function resolvePesticide(db, query, pesticideId) {
  const pests = db.pesticides || [];
  if (pesticideId) {
    const pest = pests.find((item) => item.id === pesticideId);
    return pest ? { pesticide: pest, candidates: [], via: null } : { pesticide: null, candidates: [], via: null };
  }
  const zh = normZhKey(query || "");
  const en = normEnKey(query || "");
  const exact = [];
  for (const pest of pests) {
    if (zh && pest.zh_key && pest.zh_key === zh) exact.push(pest);
    else if (en && en.length >= 2 && pest.en_key === en) exact.push(pest);
  }
  const uniq = new Map(exact.map((pest) => [pest.id, pest]));
  if (uniq.size === 1) return { pesticide: [...uniq.values()][0], candidates: [], via: null };
  if (uniq.size > 1) return { pesticide: null, candidates: [...uniq.values()], via: null };
  const via = [];
  for (const pest of pests) {
    for (const alias of pest.aliases || []) {
      if (zh && normZhKey(alias.zh || "") === zh) via.push([pest, alias]);
      else if (en && en.length >= 3 && normEnKey(alias.en || "") === en) via.push([pest, alias]);
    }
  }
  const viaUniq = new Map(via.map(([pest, alias]) => [pest.id, [pest, alias]]));
  if (viaUniq.size === 1) {
    const [pest, alias] = [...viaUniq.values()][0];
    return { pesticide: pest, candidates: [], via: alias };
  }
  if (viaUniq.size > 1) return { pesticide: null, candidates: [...viaUniq.values()].map((item) => item[0]), via: null };
  return { pesticide: null, candidates: suggestPesticides(pests, query || ""), via: null };
}

function footnoteById(db, id) {
  return (db.footnotes || []).find((note) => note.id === id);
}

export function lookup(db, query = {}) {
  const form = query.form === "dried" ? "dried" : "fresh";
  const cropHit = resolveCrop(db, query.crop, query.crop_id);
  const pestHit = resolvePesticide(db, query.pesticide, query.pesticide_id);
  const result = {
    ok: false,
    needs: null,
    form,
    crop: cropHit.crop ? publicCrop(cropHit.crop) : null,
    crop_candidates: (cropHit.candidates || []).map(publicCrop),
    alias_note: cropHit.alias_note,
    pesticide: pestHit.pesticide ? publicPest(pestHit.pesticide) : null,
    pesticide_candidates: (pestHit.candidates || []).map(publicPest),
    via: pestHit.via,
    via_note: null,
    step: null,
    basis: null,
    limit: null,
    limit_raw: null,
    star: false,
    judgment: null,
    judgment_detail: null,
    ambiguous: false,
    rows: [],
    footnotes: [],
    trace: [],
    warnings: [],
    history_keys: [],
    official_text: null,
    note5_group: null,
    buffer: null,
    buffer_warning: "",
  };
  if (!cropHit.crop) {
    result.needs = "crop";
    result.basis = result.crop_candidates.length
      ? "原料名稱對到多筆附表五作物，請選一筆，系統不會代為猜測類別。"
      : "附表五找不到這個原料，請改寫官方作物名或從候選選取。名單以「等」結尾，未列名者不自動歸類。";
    return result;
  }
  if (!pestHit.pesticide) {
    result.needs = "pesticide";
    const suggestions = result.pesticide_candidates.length
      ? []
      : suggestPesticides(db.pesticides || [], query.pesticide || "", 5);
    if (!result.pesticide_candidates.length) {
      result.pesticide_candidates = suggestions.map(publicPest);
    }
    if (result.pesticide_candidates.length && !pestHit.candidates.length) {
      result.basis = "查無完全相符的農藥名稱。若確定表中未列，依第3條不得檢出；請先核對下方相似名稱。";
      result.step = "unlisted_confirm";
      result.judgment = "待確認";
      return result;
    }
    if (pestHit.candidates.length) {
      result.basis = "農殘名稱對到多筆，請選取普通名稱或國際普通名稱。";
      return result;
    }
    const amount = parseAmount(query.value);
    const [judgment, detail] = judge(amount, "nondetect", null);
    result.needs = null;
    result.ok = true;
    result.step = "unlisted";
    result.basis = "表中未列，依第3條不得檢出";
    result.judgment = judgment;
    result.judgment_detail = detail;
    result.trace = ["附表三免訂：否", "附表一／附表二：無此農藥", "附表四禁用：否", "第3條：未列不得檢出"];
    result.official_text = `${cropHit.crop.name}／${query.pesticide || ""}／表中未列`;
    return result;
  }

  const pest = pestHit.pesticide;
  const chosen = cropHit.crop;
  const amount = parseAmount(query.value);
  const footnotes = [];
  for (const noteId of pest.note_ids || []) {
    const note = footnoteById(db, noteId);
    if (note) footnotes.push(note);
  }
  if (pestHit.via?.note && !footnotes.some((note) => note.id === pestHit.via.note)) {
    const note = footnoteById(db, pestHit.via.note);
    if (note) footnotes.push(note);
  }
  result.footnotes = footnotes;
  if (pestHit.via) {
    const alias = pestHit.via;
    result.via_note = alias.text || `${alias.note || ""}：${alias.zh || alias.en} 比照 ${pest.zh || pest.en}`;
  }

  const trace = [];
  if (pest.exempt) {
    trace.push("附表三免訂：是");
    const [judgment, detail] = judge(amount, "exempt", null);
    Object.assign(result, {
      ok: true,
      step: "exempt",
      basis: "免訂（附表三）",
      trace,
      judgment,
      judgment_detail: detail,
      official_text: `${pest.zh || pest.en}／附表三得免訂容許量`,
    });
    return result;
  }
  trace.push("附表三免訂：否");

  const rows = (pest.rows || []).filter((row) => formOk(row, form));
  const groups = new Set(chosen.groups || []);
  let specs = rows.filter((row) => specificHit(row, chosen));
  const others = rows.filter((row) => row.kind === "other_group" && groups.has(row.base) && !excluded(row, chosen));
  const subs = rows.filter((row) => row.kind === "subgroup" && groups.has(row.base) && !excluded(row, chosen));
  let cats = rows.filter((row) => row.kind === "category" && categoryHit(row, chosen));
  const broads = rows.filter(
    (row) => row.kind === "broad" && form === "dried" && (chosen.broads || []).includes(row.base) && !excluded(row, chosen),
  );
  const expected = note5Base(chosen.category, form);
  result.note5_group = expected;
  const stars = rows.filter((row) => row.kind === "note5" && row.base === expected);
  specs = preferForm(specs, form);
  cats = preferForm(cats, form);
  if (chosen.uncategorized) {
    others.length = 0;
    subs.length = 0;
    cats = [];
    broads.length = 0;
    stars.length = 0;
    trace.push("附表五未列此名稱，只比對同名個別列");
  }

  let chosenRows = [];
  let step = null;
  if (specs.length) {
    chosenRows = specs;
    step = "specific";
    trace.push(`個別標準：${specs[0].crop} ${specs[0].limit_raw}`);
  } else trace.push("個別標準：無");
  if (!step) {
    const narrowed = narrow(others);
    if (narrowed.length) {
      chosenRows = narrowed;
      step = "other_group";
      trace.push(`其他類（除外）：${narrowed.map((row) => row.crop).join("、")}`);
    } else trace.push("其他類（除外）：無");
  }
  if (!step) {
    if (subs.length) {
      chosenRows = subs;
      step = "subgroup";
      trace.push(`子類標準：${subs.map((row) => row.crop).join("、")}`);
    } else trace.push("子類標準：無");
  }
  if (!step) {
    if (cats.length) {
      chosenRows = cats;
      step = "category";
      trace.push(`類別標準：${cats.map((row) => row.crop).join("、")}`);
    } else trace.push("類別標準：無");
  }
  if (!step) {
    if (broads.length) {
      chosenRows = broads;
      step = "broad";
      trace.push(`乾燥蔬果概括列：${broads.map((row) => row.crop).join("、")}`);
    } else trace.push("乾燥蔬果概括列：無");
  }
  if (!step) {
    if (stars.length) {
      chosenRows = stars;
      step = "note5";
      trace.push(`註五：其他(${expected})*`);
    } else trace.push("註五兜底列：無");
  }

  if (chosenRows.length) {
    const ambiguous = !limitsAgree(chosenRows);
    result.ambiguous = ambiguous;
    result.rows = chosenRows.map(publicRow);
    result.history_keys = chosenRows.map((row) => row.history_key).filter(Boolean);
    result.step = step;
    result.trace = trace;
    if (ambiguous) {
      result.ok = true;
      result.basis = "有多筆可能適用標準且限值不同，請人工確認，系統不代為挑選。";
      result.judgment = "無法判定";
      result.judgment_detail = "同優先順位有不同容許量";
      result.official_text = chosenRows.map((row) => `${row.crop} ${row.limit_raw} ppm`).join("；");
      return result;
    }
    const top = chosenRows[0];
    let basis = basisFor(step, top, form);
    if (chosenRows.length > 1) {
      basis += `（另有相同限值：${chosenRows.slice(1).map((row) => row.crop).join("、")}）`;
    }
    let [judgment, detail] = judge(amount, "numeric", top.limit);
    const info = bufferInfo(top.note);
    const warning = bufferWarning(info);
    if (warning) detail = `${warning} ${detail}`.trim();
    result.buffer = info;
    result.buffer_warning = warning;
    if (step === "note5") {
      const note = footnoteById(db, "註五");
      if (note && !result.footnotes.some((item) => item.id === "註五")) result.footnotes.push(note);
    }
    Object.assign(result, {
      ok: true,
      basis,
      limit: top.limit,
      limit_raw: top.limit_raw,
      star: !!top.star,
      judgment,
      judgment_detail: detail,
      official_text: `${top.zh || ""} ${top.en || ""}／${top.crop}／${top.limit_raw} ppm／${top.note || ""}`.trim(),
    });
    return result;
  }

  if (pest.banned) {
    trace.push("附表四禁用：是");
    const [judgment, detail] = judge(amount, "nondetect", null);
    Object.assign(result, {
      ok: true,
      step: "banned",
      basis: "禁用（附表四），不得檢出",
      trace,
      judgment,
      judgment_detail: detail,
      official_text: `${pest.zh || pest.en}／附表四禁用／禁止銷售或使用：${pest.ban_use || "—"}`,
    });
    return result;
  }
  trace.push("附表四禁用：否");
  trace.push("第3條：未列不得檢出");
  const [judgment, detail] = judge(amount, "nondetect", null);
  Object.assign(result, {
    ok: true,
    step: "unlisted",
    basis: "表中未列，依第3條不得檢出",
    trace,
    judgment,
    judgment_detail: detail,
    official_text: `${pest.zh || pest.en}／${chosen.name}／表中未列`,
  });
  return result;
}
