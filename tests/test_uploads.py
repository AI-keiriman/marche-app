import re
from pathlib import Path

import pytest

from marche.uploads import delete_image, detect_media_type, save_image

# 判定は先頭のバイトしか見ないので、テストでは正しい先頭のバイト＋適当なバイトで作る
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 100
GIF = b"GIF89a" + b"\x00" * 100


@pytest.mark.parametrize(
    ("data", "media_type"),
    [
        (JPEG, "image/jpeg"),
        (PNG, "image/png"),
        (WEBP, "image/webp"),
        (GIF, None),
        (b"RIFF\x00\x00\x00\x00WAVE", None),  # RIFF でも WebP ではない（音声）
        (b"hello, world", None),
        (b"", None),
    ],
)
def test_先頭のバイトで画像の種類を判定する(data, media_type):
    assert detect_media_type(data) == media_type


def test_save_imageはUUIDのファイル名で保存する(app):
    filename = save_image(PNG, "image/png")
    assert re.fullmatch(r"[0-9a-f]{32}\.png", filename)
    assert (Path(app.config["UPLOAD_FOLDER"]) / filename).read_bytes() == PNG


def test_save_imageの拡張子は判定した種類から決まる(app):
    assert save_image(JPEG, "image/jpeg").endswith(".jpg")
    assert save_image(WEBP, "image/webp").endswith(".webp")


def test_delete_imageは写真を消す(app):
    filename = save_image(PNG, "image/png")
    delete_image(filename)
    assert not (Path(app.config["UPLOAD_FOLDER"]) / filename).exists()


def test_delete_imageはファイルが無くてもエラーにならない(app):
    delete_image("nothing.png")
    delete_image(None)


def test_delete_imageはUPLOAD_FOLDERの外を消さない(app, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("消されては困る")
    delete_image("../secret.txt")
    assert secret.exists()
