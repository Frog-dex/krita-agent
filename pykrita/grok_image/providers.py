"""Provider logic for the AI Image docker — no Krita or Qt imports.

Everything here is plain stdlib so it can be unit-tested outside Krita:
key loading, request building, response parsing and error formatting.
The docker (``grok_image.py``) wires these into QThread workers.
"""

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

GROK_URL = "https://api.x.ai/v1/images/generations"
GROK_MODEL = "grok-imagine-image"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
NANO_BANANA_MODEL = "gemini-2.5-flash-image"
IMAGEN_MODEL = "imagen-4.0-generate-001"

CONFIG_PATH = Path.home() / ".config" / "grok-image" / "config.json"
KEY_NAMES = ("XAI_API_KEY", "GOOGLE_API_KEY")
REQUEST_TIMEOUT = 180


class Provider:
    """Static description of one provider entry in the docker's combo box."""

    def __init__(self, key, label, layer_prefix, api_key_name, img2img, max_count):
        self.key = key
        self.label = label
        self.layer_prefix = layer_prefix
        self.api_key_name = api_key_name  # None => no key (local)
        self.img2img = img2img
        self.max_count = max_count  # 0 => count spinner disabled

    @property
    def is_local(self):
        return self.api_key_name is None


PROVIDERS = [
    Provider("nano_banana", "Nano Banana (img2img)", "Nano Banana", "GOOGLE_API_KEY", True, 0),
    Provider("grok", "Grok Imagine", "Grok", "XAI_API_KEY", False, 4),
    Provider("imagen", "Imagen 4", "Imagen", "GOOGLE_API_KEY", False, 4),
    Provider("local", "Local — krita-ai-diffusion", "Local", None, False, 0),
]


def provider_by_label(label):
    for provider in PROVIDERS:
        if provider.label == label:
            return provider
    raise KeyError(label)


# --- Keys ---------------------------------------------------------------------

def load_keys(config_path=None, environ=None):
    """Return {"XAI_API_KEY": ..., "GOOGLE_API_KEY": ...}.

    The config file wins over the environment; blank values fall through.
    Read at generate-time so edits apply without restarting Krita."""
    config_path = CONFIG_PATH if config_path is None else Path(config_path)
    environ = os.environ if environ is None else environ
    keys = dict.fromkeys(KEY_NAMES, "")

    try:
        data = json.loads(config_path.read_text())
    except (OSError, ValueError):
        data = {}
    if isinstance(data, dict):
        for name in KEY_NAMES:
            if data.get(name):
                keys[name] = str(data[name]).strip()

    for name, value in keys.items():
        if not value:
            keys[name] = environ.get(name, "").strip()
    return keys


def missing_key_message(key_name):
    return (
        f"No {key_name} found. Add it to:\n"
        f"  {CONFIG_PATH}\n"
        f"  or the {key_name} environment variable"
    )


# --- Requests -----------------------------------------------------------------
# Each builder returns (url, headers, body). Keys go in headers, never in URLs,
# so they don't leak into error messages or proxy logs.

def build_grok_request(api_key, prompt, count):
    return (
        GROK_URL,
        {"Authorization": "Bearer " + api_key},
        {"model": GROK_MODEL, "prompt": prompt, "n": int(count), "response_format": "b64_json"},
    )


def build_nano_banana_request(api_key, prompt, input_png=None):
    parts = [{"text": prompt}]
    if input_png:
        parts.append({
            "inline_data": {
                "mime_type": "image/png",
                "data": base64.b64encode(input_png).decode("ascii"),
            }
        })
    return (
        f"{GEMINI_BASE}/{NANO_BANANA_MODEL}:generateContent",
        {"x-goog-api-key": api_key},
        {"contents": [{"parts": parts}]},
    )


def build_imagen_request(api_key, prompt, count):
    return (
        f"{GEMINI_BASE}/{IMAGEN_MODEL}:predict",
        {"x-goog-api-key": api_key},
        {"instances": [{"prompt": prompt}], "parameters": {"sampleCount": int(count)}},
    )


# --- Responses ----------------------------------------------------------------

def parse_grok_response(data, fetch_url=None):
    """Return image bytes from ``data[].b64_json`` (or ``data[].url`` via fetch_url)."""
    images = []
    for item in data.get("data", []):
        if item.get("b64_json"):
            images.append(base64.b64decode(item["b64_json"]))
        elif item.get("url") and fetch_url is not None:
            images.append(fetch_url(item["url"]))
    return images


def parse_nano_banana_response(data):
    """Collect inline images; Gemini returns either snake_case or camelCase keys."""
    images = []
    for candidate in data.get("candidates", []):
        for part in (candidate.get("content") or {}).get("parts", []):
            inline = part.get("inline_data") or part.get("inlineData")
            if inline and inline.get("data"):
                images.append(base64.b64decode(inline["data"]))
    return images


def nano_banana_text(data):
    """Any text the model returned instead of (or alongside) an image."""
    texts = []
    for candidate in data.get("candidates", []):
        for part in (candidate.get("content") or {}).get("parts", []):
            if part.get("text"):
                texts.append(part["text"].strip())
    return " ".join(t for t in texts if t)


def parse_imagen_response(data):
    images = []
    for prediction in data.get("predictions", []):
        b64 = prediction.get("bytesBase64Encoded") or prediction.get("imageBytes")
        if b64:
            images.append(base64.b64decode(b64))
    return images


# --- HTTP ---------------------------------------------------------------------

def http_error_message(error):
    """Turn an HTTPError into a readable one-liner using the API's JSON body."""
    detail = ""
    try:
        body = error.read().decode("utf-8", "replace")
        payload = json.loads(body)
        err = payload.get("error", payload)
        if isinstance(err, dict):
            detail = err.get("message") or json.dumps(err)
        else:
            detail = str(err)
    except Exception:  # noqa: BLE001 - best effort; fall back to the status line
        detail = ""
    message = f"HTTP {error.code} {error.reason}"
    return f"{message}: {detail[:300]}" if detail else message


def post_json(url, headers, body, timeout=REQUEST_TIMEOUT):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={**headers, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise RuntimeError(http_error_message(error)) from None


def fetch_bytes(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


def generate(provider_key, api_key, prompt, count=1, input_png=None, post=post_json, fetch=fetch_bytes):
    """Run one generation synchronously and return a list of encoded image bytes.

    Raises RuntimeError with a readable message on failure."""
    if provider_key == "grok":
        data = post(*build_grok_request(api_key, prompt, count))
        return parse_grok_response(data, fetch)
    if provider_key == "nano_banana":
        data = post(*build_nano_banana_request(api_key, prompt, input_png))
        images = parse_nano_banana_response(data)
        if not images:
            text = nano_banana_text(data)
            if text:
                raise RuntimeError("No image returned. Model said: " + text[:300])
        return images
    if provider_key == "imagen":
        data = post(*build_imagen_request(api_key, prompt, count))
        return parse_imagen_response(data)
    raise ValueError("Unknown provider: " + str(provider_key))


# --- Pixels -------------------------------------------------------------------

def swap_red_blue(pixels):
    """RGBA <-> BGRA for 8-bit, 4-channel pixel bytes (the swap is its own inverse).

    Krita's RGBA/U8 colour space stores pixels as BGRA, so bytes from
    ``Document.pixelData`` are BGRA and ``Node.setPixelData`` expects BGRA."""
    src = bytes(pixels)
    if len(src) % 4:
        raise ValueError("pixel buffer length must be a multiple of 4")
    out = bytearray(src)
    out[0::4] = src[2::4]
    out[2::4] = src[0::4]
    return bytes(out)


def fit_region_label(bounds, doc_size):
    """Human-readable text for the docker's region readout."""
    if bounds is None:
        return "No active document."
    x, y, w, h = bounds
    if (x, y) == (0, 0) and (w, h) == tuple(doc_size):
        return f"No selection — using full canvas ({w}×{h})."
    return f"Selection: {w}×{h} at ({x}, {y})."
