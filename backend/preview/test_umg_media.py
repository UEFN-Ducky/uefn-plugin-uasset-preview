"""An existing thumbnail must never prevent a native-resolution upgrade."""
import io

from PIL import Image

from . import umg_preview as preview


def test_offline_source_supersedes_thumbnail_and_is_cached(tmp_path, monkeypatch):
    file = tmp_path / "Texture.uasset"
    file.write_bytes(b"test package")
    monkeypatch.setattr(preview, "_media_dir", lambda: tmp_path)
    monkeypatch.setattr(preview, "load_package", lambda _: object())
    png = io.BytesIO()
    Image.new("RGBA", (1200, 662), (10, 20, 30, 128)).save(png, "PNG")
    monkeypatch.setattr(preview, "texture_png", lambda _: png.getvalue())
    def unexpected(*_):
        raise AssertionError("full source must not call listener or thumbnail fallback")
    monkeypatch.setattr(preview, "_listener", unexpected)
    monkeypatch.setattr(preview, "extract_embedded_thumbnail", unexpected)
    (tmp_path / f"{preview._media_key(file)}.thumb.jpg").write_bytes(b"old thumbnail")
    asset = {"kind": "texture"}
    preview._fill_image(asset, "/Project/Texture.Texture", file, "Project", False)
    assert asset["quality"] == "full"
    assert (asset["width"], asset["height"]) == (1200, 662)
    monkeypatch.setattr(preview, "texture_png", unexpected)
    again = {"kind": "texture"}
    preview._fill_image(again, "/Project/Texture.Texture", file, "Project", False)
    assert again == asset


def test_bad_source_falls_back_without_breaking_widget(tmp_path, monkeypatch):
    file = tmp_path / "Texture.uasset"
    file.write_bytes(b"test package")
    monkeypatch.setattr(preview, "_media_dir", lambda: tmp_path)
    monkeypatch.setattr(preview, "load_package", lambda _: object())
    def invalid(_):
        raise ValueError("unsupported source")
    monkeypatch.setattr(preview, "texture_png", invalid)
    monkeypatch.setattr(preview, "extract_embedded_thumbnail", lambda _: b"\xff\xd8thumbnail")
    asset = {"kind": "texture"}
    preview._fill_image(asset, "/Project/Texture.Texture", file, "Project", False)
    assert asset["quality"] == "thumbnail"
