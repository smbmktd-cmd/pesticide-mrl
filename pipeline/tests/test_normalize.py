from pipeline.normalize import norm_en_key, norm_zh_key


def test_two_four_d_and_whitespace():
    assert norm_en_key("2,4-D") == norm_en_key("2‧4-D") == norm_en_key("2·4-D")
    assert norm_en_key("Abamectin ") == "abamectin"
    assert norm_zh_key("二‧四地") == norm_zh_key("二四地")
    assert norm_zh_key(" 貝芬替 ") == "貝芬替"
