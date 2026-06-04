"""Unit tests for data.attachment_meta — attachment type inference."""
from triage_dashboard.data import attachment_meta


def _m(name, url=""):
    return attachment_meta({"name": name, "url": url})


def test_image_extensions_are_flagged_is_image():
    for ext in ("png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "avif", "ico"):
        m = _m(f"screenshot.{ext}")
        assert m["is_image"] is True
        assert m["kind"] == "image"
        assert m["label"] == f"{ext.upper()} image"


def test_uppercase_and_query_in_name_still_classify_as_image():
    m = _m("Screen Shot.PNG?foo=bar")
    assert m["is_image"] is True
    assert m["label"] == "PNG image"


def test_video_and_audio_are_not_images():
    assert _m("clip.mp4") == {"label": "MP4 video", "kind": "video", "is_image": False}
    assert _m("tone.wav") == {"label": "WAV audio", "kind": "audio", "is_image": False}


def test_log_archive_json_html():
    assert _m("log.txt-main.3560.moz_log")["kind"] == "log"
    assert _m("firefox-rdd-leak.zip")["kind"] == "archive"
    assert _m("memory-report.json.gz") == {"label": "JSON", "kind": "data", "is_image": False}
    assert _m("data.json")["label"] == "JSON"
    assert _m("Webaudio nodes.html")["kind"] == "html"


def test_profiler_by_name_or_host():
    by_name = _m("Firefox Profiler capture", "https://example.test/x")
    assert by_name["kind"] == "profiler" and by_name["is_image"] is False
    by_host = _m("capture", "https://share.firefox.dev/3PTxDGK")
    assert by_host["kind"] == "profiler"
    assert _m("p", "https://profiler.firefox.com/public/abc")["kind"] == "profiler"


def test_unknown_extension_and_no_extension():
    assert _m("weird.xyz") == {"label": "XYZ", "kind": "file", "is_image": False}
    assert _m("just-a-name") == {"label": "link", "kind": "link", "is_image": False}


def test_missing_or_empty_fields_do_not_raise():
    assert attachment_meta({})["kind"] == "link"
    assert attachment_meta({"name": None, "url": None})["kind"] == "link"


# ─── content_type takes precedence (exact MIME detection) ────────────

def test_content_type_image_detected_despite_descriptive_name():
    # The real-world miss: a screenshot named without a file extension.
    m = attachment_meta({"name": "screenshot of about:processes",
                         "content_type": "image/png"})
    assert m == {"label": "PNG image", "kind": "image", "is_image": True}


def test_content_type_svg_xml_label_cleaned():
    m = attachment_meta({"name": "diagram", "content_type": "image/svg+xml"})
    assert m["is_image"] is True
    assert m["label"] == "SVG image"


def test_content_type_video_is_not_image():
    m = attachment_meta({"name": "screen recording (symptom)",
                         "content_type": "video/mp4"})
    assert m == {"label": "MP4 video", "kind": "video", "is_image": False}


def test_content_type_with_charset_param_is_handled():
    m = attachment_meta({"name": "x", "content_type": "image/jpeg; charset=binary"})
    assert m["is_image"] is True and m["label"] == "JPEG image"


def test_content_type_overrides_misleading_extension():
    # A .txt that BMO reports as an image → trust the MIME type.
    m = attachment_meta({"name": "evidence.txt", "content_type": "image/gif"})
    assert m["is_image"] is True


def test_non_media_content_type_falls_back_to_extension():
    # application/* isn't media → extension heuristic still labels it.
    m = attachment_meta({"name": "report.json", "content_type": "application/json"})
    assert m == {"label": "JSON", "kind": "data", "is_image": False}


def test_no_content_type_still_uses_extension():
    assert attachment_meta({"name": "shot.png"})["is_image"] is True
