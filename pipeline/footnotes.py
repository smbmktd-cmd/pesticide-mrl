"""Official 附表一 footnotes. PDF extraction wins; this text is the fallback."""

from __future__ import annotations

import re

FALLBACK = [
    {
        "id": "註一",
        "text": "二硫代胺基甲酸鹽類之容許量以 CS2 計，其適用於二硫代胺基甲酸鹽類農藥之殘留總量，包括：1. 使用 ziram（益穗單劑成分之一）、免得爛（metiram）、鎳乃浦（sankel）殘留之 dimethyldithiocarbamates。2. 使用鋅錳乃浦（mancozeb）、錳乃浦（maneb）、甲基鋅乃浦（propineb）、鐵鋅錳乃浦及 cufraneb（銅合浦單劑）殘留之 ethylenebis(dithiocarbamate)s。3. 使用益地安（ETM）、得恩地（thiram）及富爾邦（Ferbam）。",
    },
    {
        "id": "註二",
        "text": "貝芬替之容許含量適用於免賴得、甲基多保淨產生代謝物之貝芬替殘留、或直接使用貝芬替之殘留。",
    },
    {
        "id": "註三",
        "text": "Iminoctadine 之容許量適用於 iminoctadine、iminoctadine triacetate 或 iminoctadine tris(albesilate)之殘留。",
    },
    {
        "id": "註四",
        "text": "農藥相關異構物之殘留以總量計算，包括：1. 賽滅寧（cypermethrin）之容許量，適用於賽滅寧及亞滅寧（alphacypermethrin）之殘留總量。2. 賽扶寧（cyfluthrin）之容許量，適用於賽扶寧及貝他賽扶寧（beta-cyfluthrin）之殘留總量。3. 芬化利（fenvalerate）之容許量，適用於芬化利及益化利（esfenvalerate）之殘留總量。4. 滅達樂（metalaxyl）之容許量，適用於滅達樂及右滅達樂（metalaxyl-M）之殘留總量。5. 莫多草（metolachlor）之容許量，適用於左旋及右旋莫多草之殘留總量。",
    },
    {
        "id": "註五",
        "text": "表中加註「*」不代表可使用農藥之作物範圍，係依公告檢驗方法之定量極限訂定。除該農藥已訂定在特定作物之殘留容許量者外，其餘作物應符合本項規定。相同定量極限者比照適用，例如乾燥香辛植物及其他草木本植物適用其他(茶類)*。自中華民國一百十二年一月一日起，其他(蔬果類)* 對應檢體 I 類、其他(穀類)* 對應 II 類、其他(茶類)* 對應 III 類。",
    },
    {
        "id": "註六",
        "text": "大滅松（Dimethoate）之容許量，適用於大滅松及歐滅松（Omethoate）之殘留總量。歐滅松之殘留，應同時符合歐滅松之容許量。",
    },
    {
        "id": "註七",
        "text": "磷化氫（phosphine）之容許量適用於好達勝（aluminium phosphide）、磷化鎂（magnesium phosphide）、磷化鋅（zinc phosphide）之殘留，或直接使用磷化氫之殘留。",
    },
    {
        "id": "註八",
        "text": "自中華民國一百十三年四月一日起，本表所列特定作物之容許量經發布刪除者，於流通緩衝期間殘留量符合刪除前容許量者，可繼續製造、加工、調配、貯存、販賣、輸入、輸出、作為贈品或公開陳列。緩衝期：生鮮食用花卉三個月；蔬果類、香辛植物及草木本植物（含乾燥食用花卉）一年；乾豆類、咖啡豆、可可豆二年；米類、雜糧類、茶類三年。",
    },
]

# 這些名稱沒有自己的列，查詢時改走註所指定的容許量。
REDIRECTS = [
    {
        "note": "註一",
        "target": "dithiocarbamates",
        "aliases": [
            {"zh": "鋅錳乃浦", "en": "Mancozeb"},
            {"zh": "錳乃浦", "en": "Maneb"},
            {"zh": "甲基鋅乃浦", "en": "Propineb"},
            {"zh": "益穗", "en": "Ziram"},
            {"zh": "免得爛", "en": "Metiram"},
            {"zh": "得恩地", "en": "Thiram"},
            {"zh": "富爾邦", "en": "Ferbam"},
            {"zh": "益地安", "en": "ETM"},
            {"zh": "鎳乃浦", "en": "Sankel"},
            {"zh": "銅合浦", "en": "Cufraneb"},
        ],
    },
    {
        "note": "註二",
        "target": "carbendazim",
        "aliases": [
            {"zh": "免賴得", "en": "Benomyl"},
            {"zh": "甲基多保淨", "en": "Thiophanate-methyl"},
        ],
    },
    {
        "note": "註四",
        "target": "cypermethrin",
        "aliases": [{"zh": "亞滅寧", "en": "Alphacypermethrin"}, {"zh": "亞滅寧", "en": "alpha-Cypermethrin"}],
    },
    {
        "note": "註四",
        "target": "cyfluthrin",
        "aliases": [{"zh": "貝他賽扶寧", "en": "beta-Cyfluthrin"}],
    },
    {
        "note": "註四",
        "target": "fenvalerate",
        "aliases": [{"zh": "益化利", "en": "Esfenvalerate"}],
    },
    {
        "note": "註四",
        "target": "metalaxyl",
        "aliases": [{"zh": "右滅達樂", "en": "Metalaxyl-M"}],
    },
    {
        "note": "註四",
        "target": "metolachlor",
        "aliases": [{"zh": "左旋莫多草", "en": "S-Metolachlor"}],
    },
    {
        "note": "註七",
        "target": "phosphine",
        "aliases": [
            {"zh": "好達勝", "en": "Aluminium phosphide"},
            {"zh": "磷化鎂", "en": "Magnesium phosphide"},
            {"zh": "磷化鋅", "en": "Zinc phosphide"},
        ],
    },
]

NOTE_ON_KEY = {
    "dithiocarbamates": ["註一"],
    "carbendazim": ["註二"],
    "iminoctadine": ["註三"],
    "cypermethrin": ["註四"],
    "cyfluthrin": ["註四"],
    "fenvalerate": ["註四"],
    "metalaxyl": ["註四"],
    "metolachlor": ["註四"],
    "dimethoate": ["註六"],
    "omethoate": ["註六"],
    "phosphine": ["註七"],
}


def parse_footnotes(plain_text: str | None) -> list[dict]:
    if not plain_text or "註一" not in plain_text or "註五" not in plain_text:
        return [dict(item) for item in FALLBACK]
    index = plain_text.rfind("註一")
    chunk = plain_text[index:]
    chunk = re.sub(r"[ \t]*\n[ \t]*", "", chunk)
    chunk = re.sub(r"\s+", " ", chunk).strip()
    parts = re.split(r"(註[一二三四五六七八])", chunk)
    notes = []
    current = None
    for part in parts:
        if re.fullmatch(r"註[一二三四五六七八]", part):
            if current:
                notes.append(current)
            current = {"id": part, "text": ""}
        elif current is not None:
            current["text"] += part
    if current:
        notes.append(current)
    cleaned = []
    for note in notes:
        text = re.sub(r"\s+", " ", note["text"]).strip(" ：:.")
        if len(text) < 8:
            continue
        cleaned.append({"id": note["id"], "text": text})
    ids = {item["id"] for item in cleaned}
    if not {"註二", "註五"} <= ids:
        return [dict(item) for item in FALLBACK]
    return cleaned
