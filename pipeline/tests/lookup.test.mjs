import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { lookup } from "../../site/js/lookup.js";

const dbPath = process.argv[2] || new URL("../../site/data/db.json", import.meta.url);
const db = JSON.parse(readFileSync(dbPath, "utf8"));

function check(query, expect) {
  const result = lookup(db, query);
  for (const [key, value] of Object.entries(expect)) {
    assert.equal(result[key], value, `${JSON.stringify(query)} ${key}: ${result[key]} !== ${value}`);
  }
  return result;
}

const bitter = check(
  { crop: "苦瓜", pesticide: "貝芬替", value: "0.4", form: "fresh" },
  { limit: "0.5", basis: "依瓜菜類標準", judgment: "合格", step: "category" },
);
assert.equal(bitter.crop.category, "瓜菜類");
assert.ok(!bitter.basis.includes("瓜果"));

check(
  { crop: "番茄", pesticide: "貝芬替", value: "1.2" },
  { limit: "1", basis: "依其他果菜類(洛神葵除外)標準", judgment: "超標", step: "other_group" },
);
const melon = check(
  { crop: "西瓜", pesticide: "貝芬替", value: "2" },
  { limit: "2", basis: "依瓜果類標準", judgment: "合格", step: "category" },
);
assert.equal(melon.crop.category, "瓜果類");

check({ crop: "洛神葵", pesticide: "貝芬替" }, { basis: "依洛神葵個別標準", limit: "0.5", step: "specific" });
check({ crop: "小黃瓜", pesticide: "Acequinocyl" }, { basis: "依小黃瓜個別標準", step: "specific" });
check({ crop: "胡瓜", pesticide: "Acequinocyl" }, { basis: "依胡瓜(小黃瓜除外)個別標準" });
check({ crop: "苦瓜", pesticide: "蘇力菌", value: "1" }, { basis: "免訂（附表三）", judgment: "免訂" });
check(
  { crop: "番茄", pesticide: "滴滴涕", value: "0.01" },
  { basis: "禁用（附表四），不得檢出", judgment: "超標" },
);
check(
  { crop: "苦瓜", pesticide: "完全沒有這種藥xyz", value: "0.2" },
  { basis: "表中未列，依第3條不得檢出", judgment: "超標" },
);
const dried = check(
  { crop: "薄荷", pesticide: "貝芬替", form: "dried" },
  { limit: "0.05", step: "note5" },
);
assert.ok(dried.basis.startsWith("依其他(茶類)*，註五定量極限"));
const ambiguous = lookup(db, { crop: "花豆", pesticide: "貝芬替" });
assert.equal(ambiguous.needs, "crop");
assert.deepEqual(ambiguous.crop_candidates.map((item) => item.name).sort(), ["花豆(乾)", "花豆(鮮)"]);

console.log("js lookup ok");
