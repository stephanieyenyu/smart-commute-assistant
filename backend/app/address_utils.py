import re


CITY_ALIAS_MAP = {
    "台北市": "臺北市",
    "臺北市": "臺北市",
    "taipei": "臺北市",
    "taipei city": "臺北市",

    "新北市": "新北市",
    "new taipei": "新北市",
    "new taipei city": "新北市",

    "桃園市": "桃園市",
    "taoyuan": "桃園市",
    "taoyuan city": "桃園市",

    "台中市": "臺中市",
    "臺中市": "臺中市",
    "taichung": "臺中市",
    "taichung city": "臺中市",

    "台南市": "臺南市",
    "臺南市": "臺南市",
    "tainan": "臺南市",
    "tainan city": "臺南市",

    "高雄市": "高雄市",
    "kaohsiung": "高雄市",
    "kaohsiung city": "高雄市",

    "基隆市": "基隆市",
    "keelung": "基隆市",
    "keelung city": "基隆市",

    "新竹市": "新竹市",
    "hsinchu city": "新竹市",

    "嘉義市": "嘉義市",
    "chiayi city": "嘉義市",

    "新竹縣": "新竹縣",
    "hsinchu county": "新竹縣",

    "苗栗縣": "苗栗縣",
    "miaoli": "苗栗縣",
    "miaoli county": "苗栗縣",

    "彰化縣": "彰化縣",
    "changhua": "彰化縣",
    "changhua county": "彰化縣",

    "南投縣": "南投縣",
    "nantou": "南投縣",
    "nantou county": "南投縣",

    "雲林縣": "雲林縣",
    "yunlin": "雲林縣",
    "yunlin county": "雲林縣",

    "嘉義縣": "嘉義縣",
    "chiayi county": "嘉義縣",

    "屏東縣": "屏東縣",
    "pingtung": "屏東縣",
    "pingtung county": "屏東縣",

    "宜蘭縣": "宜蘭縣",
    "yilan": "宜蘭縣",
    "yilan county": "宜蘭縣",

    "花蓮縣": "花蓮縣",
    "hualien": "花蓮縣",
    "hualien county": "花蓮縣",

    "台東縣": "臺東縣",
    "臺東縣": "臺東縣",
    "taitung": "臺東縣",
    "taitung county": "臺東縣",

    "澎湖縣": "澎湖縣",
    "penghu": "澎湖縣",
    "penghu county": "澎湖縣",

    "金門縣": "金門縣",
    "kinmen": "金門縣",
    "kinmen county": "金門縣",

    "連江縣": "連江縣",
    "lienchiang": "連江縣",
    "lienchiang county": "連江縣",
}

GREETING_WORDS = {
    "嗨", "你好", "哈囉", "哈喽", "早安", "晚安",
    "hi", "hello", "hey"
}


def normalize_city_name(city_name: str | None) -> str | None:
    if not city_name:
        return None

    raw = city_name.strip()
    lowered = raw.lower()

    if raw in CITY_ALIAS_MAP:
        return CITY_ALIAS_MAP[raw]
    if lowered in CITY_ALIAS_MAP:
        return CITY_ALIAS_MAP[lowered]

    return raw


def extract_city_from_text(text: str | None) -> str | None:
    if not text:
        return None

    raw = text.strip()
    lowered = raw.lower()

    english_aliases = [k for k in CITY_ALIAS_MAP.keys() if re.search(r"[a-zA-Z]", k)]
    for alias in sorted(english_aliases, key=len, reverse=True):
        if alias in lowered:
            return CITY_ALIAS_MAP[alias]

    chinese_aliases = [k for k in CITY_ALIAS_MAP.keys() if not re.search(r"[a-zA-Z]", k)]
    for alias in sorted(chinese_aliases, key=len, reverse=True):
        if alias in raw:
            return CITY_ALIAS_MAP[alias]

    return None


def normalize_township_name(township_name: str | None) -> str | None:
    if not township_name:
        return None

    raw = township_name.strip()

    if re.search(r"[\u4e00-\u9fff]", raw):
        return raw

    cleaned = raw
    suffixes = [
        "district", "dist.", "dist",
        "township", "town",
        "village", "city", "county"
    ]

    lowered = cleaned.lower()
    for suffix in suffixes:
        if lowered.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip(", ").strip()
            break

    if not cleaned:
        return raw

    return cleaned.title()


def looks_like_address(text: str) -> bool:
    if not text:
        return False

    raw = text.strip()
    lowered = raw.lower()

    if lowered in GREETING_WORDS or raw in GREETING_WORDS:
        return False

    zh_keywords = ["市", "縣", "區", "鄉", "鎮", "村", "里", "路", "街", "號", "巷", "弄", "段"]
    if any(keyword in raw for keyword in zh_keywords):
        return True

    en_keywords = [
        "road", "rd", "street", "st", "lane", "ln", "alley", "aly",
        "avenue", "ave", "boulevard", "blvd",
        "section", "sec",
        "district", "dist",
        "city", "county", "township", "town",
        "no.", "no", "floor", "fl"
    ]

    for keyword in en_keywords:
        if re.search(rf"\b{re.escape(keyword)}\b", lowered):
            return True

    if re.search(r"\d", raw) and "," in raw:
        return True

    return False