"""商品の写真の保存と削除。拡張子ではなく、ファイルの中身（先頭のバイト）で種類を確かめる"""

import uuid
from pathlib import Path

from flask import current_app

# 保存するときの拡張子は、アップロードした人が付けた名前ではなく、判定した種類から決める
EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def detect_media_type(data: bytes) -> str | None:
    """先頭のバイト（マジックナンバー）で JPEG / PNG / WebP を判定する。どれでもなければ None"""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _upload_folder() -> Path:
    return Path(current_app.config["UPLOAD_FOLDER"])


def save_image(data: bytes, media_type: str) -> str:
    """UUID のファイル名で UPLOAD_FOLDER に保存し、ファイル名を返す"""
    filename = uuid.uuid4().hex + EXTENSIONS[media_type]
    folder = _upload_folder()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_bytes(data)
    return filename


def delete_image(filename: str | None) -> None:
    """写真を消す。filename が空のときや、ファイルが無いときは何もしない"""
    if not filename:
        return
    # パスの部分を取り除き、UPLOAD_FOLDER の外のファイルは消せないようにする
    name = Path(filename).name
    if name in ("", ".", ".."):
        return
    (_upload_folder() / name).unlink(missing_ok=True)
