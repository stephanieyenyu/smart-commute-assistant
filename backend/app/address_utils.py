import re


GREETING_WORDS = {
    "hi",
    "hello",
    "hey",
    "嗨",
    "你好",
    "哈囉",
    "哈喽",
    "安安",
}

CITY_ALIASES = {
    "臺北市": "臺北市",
    "台北市": "臺北市",
    "taipei city": "臺北市",
    "taipei": "臺北市",

    "新北市": "新北市",
    "new taipei city": "新北市",
    "new taipei": "新北市",
    "newtaipei": "新北市",

    "桃園市": "桃園市",
    "taoyuan city": "桃園市",
    "taoyuan": "桃園市",

    "臺中市": "臺中市",
    "台中市": "臺中市",
    "taichung city": "臺中市",
    "taichung": "臺中市",

    "臺南市": "臺南市",
    "台南市": "臺南市",
    "tainan city": "臺南市",
    "tainan": "臺南市",

    "高雄市": "高雄市",
    "kaohsiung city": "高雄市",
    "kaohsiung": "高雄市",

    "基隆市": "基隆市",
    "keelung city": "基隆市",
    "keelung": "基隆市",

    "新竹市": "新竹市",
    "hsinchu city": "新竹市",
    "hsinchu": "新竹市",

    "新竹縣": "新竹縣",
    "hsinchu county": "新竹縣",

    "苗栗縣": "苗栗縣",
    "miaoli county": "苗栗縣",

    "彰化縣": "彰化縣",
    "changhua county": "彰化縣",

    "南投縣": "南投縣",
    "nantou county": "南投縣",

    "雲林縣": "雲林縣",
    "yunlin county": "雲林縣",

    "嘉義市": "嘉義市",
    "chiayi city": "嘉義市",
    "chiayi": "嘉義市",

    "嘉義縣": "嘉義縣",
    "chiayi county": "嘉義縣",

    "屏東縣": "屏東縣",
    "pingtung county": "屏東縣",

    "宜蘭縣": "宜蘭縣",
    "yilan county": "宜蘭縣",

    "花蓮縣": "花蓮縣",
    "hualien county": "花蓮縣",

    "臺東縣": "臺東縣",
    "台東縣": "臺東縣",
    "taitung county": "臺東縣",
    "taitung": "臺東縣",

    "澎湖縣": "澎湖縣",
    "penghu county": "澎湖縣",

    "金門縣": "金門縣",
    "kinmen county": "金門縣",

    "連江縣": "連江縣",
    "lienchiang county": "連江縣",
}

CITY_PATTERNS = [
    (r"(臺北市|台北市|Taipei City|Taipei)", "臺北市"),
    (r"(新北市|New Taipei City|New Taipei|NewTaipei)", "新北市"),
    (r"(桃園市|Taoyuan City|Taoyuan)", "桃園市"),
    (r"(臺中市|台中市|Taichung City|Taichung)", "臺中市"),
    (r"(臺南市|台南市|Tainan City|Tainan)", "臺南市"),
    (r"(高雄市|Kaohsiung City|Kaohsiung)", "高雄市"),
    (r"(基隆市|Keelung City|Keelung)", "基隆市"),
    (r"(新竹市|Hsinchu City|Hsinchu)", "新竹市"),
    (r"(新竹縣|Hsinchu County)", "新竹縣"),
    (r"(苗栗縣|Miaoli County)", "苗栗縣"),
    (r"(彰化縣|Changhua County)", "彰化縣"),
    (r"(南投縣|Nantou County)", "南投縣"),
    (r"(雲林縣|Yunlin County)", "雲林縣"),
    (r"(嘉義市|Chiayi City|Chiayi)", "嘉義市"),
    (r"(嘉義縣|Chiayi County)", "嘉義縣"),
    (r"(屏東縣|Pingtung County)", "屏東縣"),
    (r"(宜蘭縣|Yilan County)", "宜蘭縣"),
    (r"(花蓮縣|Hualien County)", "花蓮縣"),
    (r"(臺東縣|台東縣|Taitung County|Taitung)", "臺東縣"),
    (r"(澎湖縣|Penghu County)", "澎湖縣"),
    (r"(金門縣|Kinmen County)", "金門縣"),
    (r"(連江縣|Lienchiang County)", "連江縣"),
]

CHINESE_ADDRESS_HINTS = ["市", "區", "鄉", "鎮", "里", "村", "路", "街", "段", "巷", "弄", "號", "樓"]
ENGLISH_ADDRESS_HINTS = [
    "road", "rd", "street", "st", "avenue", "ave", "lane", "ln",
    "section", "sec", "no.", "no", "district", "city", "county", "floor", "fl",
]


def normalise_text(text: str | None) -> str:
    if not text:
        return ""
    return text.strip().replace("\u3000", " ")


def normalize_city_name(city_name: str | None) -> str | None:
    value = normalise_text(city_name)
    if not value:
        return None

    direct = CITY_ALIASES.get(value)
    if direct:
        return direct

    lower_value = value.lower()
    direct_lower = CITY_ALIASES.get(lower_value)
    if direct_lower:
        return direct_lower

    for pattern, canonical in CITY_PATTERNS:
        if re.search(pattern, value, flags=re.IGNORECASE):
            return canonical

    return None


def extract_city_from_text(text: str | None) -> str | None:
    value = normalise_text(text)
    if not value:
        return None

    for pattern, city_name in CITY_PATTERNS:
        if re.search(pattern, value, flags=re.IGNORECASE):
            return city_name

    return None


def looks_like_address(text: str | None) -> bool:
    value = normalise_text(text)
    if not value:
        return False

    lower = value.lower()
    if lower in GREETING_WORDS or value in GREETING_WORDS:
        return False

    if extract_city_from_text(value):
        return True

    if any(token in value for token in CHINESE_ADDRESS_HINTS):
        return True

    if re.search(r"\d+", value) and any(token in lower for token in ENGLISH_ADDRESS_HINTS):
        return True

    if re.search(r"\b(taipei|new taipei|taoyuan|taichung|tainan|kaohsiung)\b", lower):
        return True

    return False