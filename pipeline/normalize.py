"""Text normalization for pesticide and crop names.

Raw source strings are always kept. These helpers only build match keys.
"""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")
_INTERPUNCT = str.maketrans(
    {
        "‧": ",",
        "·": ",",
        "•": ",",
        "・": ",",
        "．": ",",
        "､": ",",
        "，": ",",
    }
)
_ZH_STRIP = str.maketrans(
    {
        "‧": "",
        "·": "",
        "•": "",
        "・": "",
        "．": "",
        "（": "(",
        "）": ")",
        "　": "",
    }
)


def clean_ws(value: str | None) -> str:
    if not value:
        return ""
    return _WS.sub(" ", value.replace("\u3000", " ").replace("\xa0", " ")).strip()


def squash_ws(value: str | None) -> str:
    if not value:
        return ""
    return _WS.sub("", value.replace("\u3000", "").replace("\xa0", ""))


def norm_label(value: str | None) -> str:
    """Crop-label key: trim, drop spaces, unify parentheses."""
    text = squash_ws(value)
    return text.replace("（", "(").replace("）", ")").replace("，", "、")


def norm_en_key(value: str | None) -> str:
    """Loose English key. 2,4-D and 2‧4-D both become 24d."""
    text = clean_ws(value).lower().translate(_INTERPUNCT)
    return re.sub(r"[^0-9a-z]+", "", text)


def norm_zh_key(value: str | None) -> str:
    text = squash_ws(value).translate(_ZH_STRIP)
    return text.casefold()


def norm_limit_number(value: str | None) -> tuple[str, bool]:
    """Return (numeric string, star flag). Star marks 註五定量極限."""
    text = clean_ws(value).replace("＊", "*")
    star = text.endswith("*")
    number = text[:-1].strip() if star else text
    if not number:
        raise ValueError(f"empty limit: {value!r}")
    return number, star
