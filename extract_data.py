"""
index.html에서 카드/빌드 데이터를 추출해서 cards.json으로 저장
"""
import re
import json

with open("index.html", encoding="utf-8") as f:
    html = f.read()

VAR_NAMES = [
    "BASIC_CARDS", "COLORLESS_CARDS",
    "IC_CARDS", "SI_CARDS", "DE_CARDS", "NE_CARDS", "RE_CARDS",
    "IC_ARCHS", "SI_ARCHS", "DE_ARCHS", "NE_ARCHS", "RE_ARCHS",
]

def extract_js_array(source, var_name):
    pattern = rf"const {var_name}\s*=\s*(\[)"
    m = re.search(pattern, source)
    if not m:
        return None
    start = m.start(1)
    depth = 0
    for i, ch in enumerate(source[start:], start):
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                return source[start:i+1]
    return None

def js_to_json(js_str):
    # 키에 따옴표 추가: {n: -> {"n":
    js_str = re.sub(r'([{,])\s*([a-zA-Z_]\w*)\s*:', r'\1"\2":', js_str)
    # 작은따옴표 → 큰따옴표
    js_str = re.sub(r"'([^']*)'", r'"\1"', js_str)
    # 후행 콤마 제거
    js_str = re.sub(r',\s*([}\]])', r'\1', js_str)
    return js_str

data = {}
for name in VAR_NAMES:
    raw = extract_js_array(html, name)
    if raw:
        try:
            parsed = json.loads(js_to_json(raw))
            data[name] = parsed
            print(f"OK {name}: {len(parsed)}")
        except json.JSONDecodeError as e:
            print(f"FAIL {name}: JSON parsing error - {e}")
            data[name] = []
    else:
        print(f"FAIL {name}: not found")
        data[name] = []

# 캐릭터 매핑
CHARACTER_MAP = {
    "IC": "Ironclad",
    "SI": "Silent",
    "DE": "Defect",
    "NE": "Necrobinder",
    "RE": "Regent",
}

output = {
    "characters": CHARACTER_MAP,
    "cards": {
        "basic": data.get("BASIC_CARDS", []),
        "colorless": data.get("COLORLESS_CARDS", []),
        "IC": data.get("IC_CARDS", []),
        "SI": data.get("SI_CARDS", []),
        "DE": data.get("DE_CARDS", []),
        "NE": data.get("NE_CARDS", []),
        "RE": data.get("RE_CARDS", []),
    },
    "archs": {
        "IC": data.get("IC_ARCHS", []),
        "SI": data.get("SI_ARCHS", []),
        "DE": data.get("DE_ARCHS", []),
        "NE": data.get("NE_ARCHS", []),
        "RE": data.get("RE_ARCHS", []),
    }
}

with open("cards.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print("\nDone: cards.json saved")
