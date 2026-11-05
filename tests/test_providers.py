import base64
import io
import json
import urllib.error

import pytest

from conftest import load_module

providers = load_module("providers", "pykrita/grok_image/providers.py")


def b64(data):
    return base64.b64encode(data).decode("ascii")


def test_load_keys_prefers_config_over_env(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"XAI_API_KEY": " from-file ", "GOOGLE_API_KEY": ""}))
    keys = providers.load_keys(cfg, {"XAI_API_KEY": "from-env", "GOOGLE_API_KEY": "g-env"})
    assert keys == {"XAI_API_KEY": "from-file", "GOOGLE_API_KEY": "g-env"}


def test_load_keys_survives_missing_or_broken_config(tmp_path):
    assert providers.load_keys(tmp_path / "nope.json", {}) == {"XAI_API_KEY": "", "GOOGLE_API_KEY": ""}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert providers.load_keys(bad, {"GOOGLE_API_KEY": "x"})["GOOGLE_API_KEY"] == "x"
    listy = tmp_path / "list.json"
    listy.write_text("[1, 2]")
    assert providers.load_keys(listy, {})["XAI_API_KEY"] == ""


def test_google_keys_go_in_headers_not_urls():
    for url, headers, _ in (
        providers.build_nano_banana_request("SECRET", "a frog", None),
        providers.build_imagen_request("SECRET", "a frog", 2),
    ):
        assert "SECRET" not in url
        assert headers["x-goog-api-key"] == "SECRET"


def test_grok_request_shape():
    url, headers, body = providers.build_grok_request("k", "frog wizard", 3)
    assert url == providers.GROK_URL
    assert headers == {"Authorization": "Bearer k"}
    assert body == {"model": providers.GROK_MODEL, "prompt": "frog wizard", "n": 3, "response_format": "b64_json"}


def test_nano_banana_img2img_attaches_png():
    _, _, body = providers.build_nano_banana_request("k", "paint it", b"\x89PNG")
    parts = body["contents"][0]["parts"]
    assert parts[0] == {"text": "paint it"}
    assert parts[1]["inline_data"] == {"mime_type": "image/png", "data": b64(b"\x89PNG")}


def test_parse_nano_banana_handles_both_spellings():
    data = {"candidates": [{"content": {"parts": [
        {"text": "here you go"},
        {"inline_data": {"data": b64(b"one")}},
        {"inlineData": {"mimeType": "image/png", "data": b64(b"two")}},
    ]}}, {"content": None}]}
    assert providers.parse_nano_banana_response(data) == [b"one", b"two"]
    assert providers.nano_banana_text(data) == "here you go"


def test_parse_grok_and_imagen():
    assert providers.parse_grok_response(
        {"data": [{"b64_json": b64(b"a")}, {"url": "http://x/img"}]}, fetch_url=lambda u: b"fetched:" + u.encode()
    ) == [b"a", b"fetched:http://x/img"]
    assert providers.parse_imagen_response(
        {"predictions": [{"bytesBase64Encoded": b64(b"p1")}, {"imageBytes": b64(b"p2")}, {}]}
    ) == [b"p1", b"p2"]


def test_generate_routes_and_reports_text_only_reply():
    calls = []

    def fake_post(url, headers, body):
        calls.append(url)
        if "generateContent" in url:
            return {"candidates": [{"content": {"parts": [{"text": "I can't draw that."}]}}]}
        return {"predictions": [{"bytesBase64Encoded": b64(b"img")}]}

    assert providers.generate("imagen", "k", "frog", 1, post=fake_post) == [b"img"]
    with pytest.raises(RuntimeError, match="I can't draw that"):
        providers.generate("nano_banana", "k", "frog", post=fake_post)
    with pytest.raises(ValueError):
        providers.generate("nope", "k", "frog", post=fake_post)
    assert len(calls) == 2


def test_http_error_message_uses_api_body():
    body = json.dumps({"error": {"code": 400, "message": "API key not valid."}}).encode()
    err = urllib.error.HTTPError("https://x", 400, "Bad Request", {}, io.BytesIO(body))
    assert providers.http_error_message(err) == "HTTP 400 Bad Request: API key not valid."
    err = urllib.error.HTTPError("https://x", 500, "Server Error", {}, io.BytesIO(b"<html>"))
    assert providers.http_error_message(err) == "HTTP 500 Server Error"


def test_swap_red_blue_roundtrip():
    rgba = bytes([255, 10, 0, 200, 1, 2, 3, 4])
    bgra = providers.swap_red_blue(rgba)
    assert bgra == bytes([0, 10, 255, 200, 3, 2, 1, 4])
    assert providers.swap_red_blue(bgra) == rgba
    with pytest.raises(ValueError):
        providers.swap_red_blue(b"abc")


def test_region_label():
    assert providers.fit_region_label(None, (0, 0)) == "No active document."
    assert "full canvas (100×50)" in providers.fit_region_label((0, 0, 100, 50), (100, 50))
    assert providers.fit_region_label((5, 6, 10, 20), (100, 50)) == "Selection: 10×20 at (5, 6)."


def test_provider_table():
    labels = [p.label for p in providers.PROVIDERS]
    assert len(set(labels)) == len(labels)
    assert [p.key for p in providers.PROVIDERS if p.img2img] == ["nano_banana"]
    assert providers.provider_by_label("Imagen 4").api_key_name == "GOOGLE_API_KEY"
    assert providers.provider_by_label("Local — krita-ai-diffusion").is_local
