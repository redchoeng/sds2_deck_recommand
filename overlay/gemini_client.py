import io
from pathlib import Path
from PIL import Image
from google import genai
from google.genai import types

_HERE = Path(__file__).parent
# sds2_ai_helper/.api_key 공유 (없으면 overlay/.api_key 사용)
_SHARED = _HERE.parent.parent / "sds2_ai_helper" / ".api_key"
API_KEY_FILE = _SHARED if _SHARED.exists() else _HERE / ".api_key"

_client = None


def init(api_key: str = None):
    global _client
    if api_key is None:
        api_key = _load_api_key()
    _client = genai.Client(api_key=api_key)


def _load_api_key() -> str:
    if API_KEY_FILE.exists():
        return API_KEY_FILE.read_text().strip()
    key = input("Gemini API 키를 입력하세요: ").strip()
    (_HERE / ".api_key").write_text(key)
    return key


def is_ready() -> bool:
    return _client is not None


def ask(prompt: str, image: Image.Image = None) -> str:
    if _client is None:
        raise RuntimeError("gemini_client.init()을 먼저 호출하세요")
    parts = [prompt]
    if image is not None:
        if image.width > 1280:
            ratio = 1280 / image.width
            image = image.resize((1280, int(image.height * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=85)
        parts.append(types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"))
    response = _client.models.generate_content(
        model="gemini-2.5-flash",
        contents=parts,
    )
    return response.text.strip()
