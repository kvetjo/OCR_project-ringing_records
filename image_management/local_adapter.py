"""
Local File System Image Storage Adapter
Handles image storage and retrieval from local file system.
"""
import os
import base64
from pathlib import Path
from typing import List, Tuple, Dict, Any


def get_mime_type(path: Path) -> str:
    """
    Return correct MIME type based on file extension.
    For PDF returns 'application/pdf' directly based on extension.
    For images uses the file content as fallback.
    """
    ext = path.suffix.lower()
    if ext == ".pdf":
        return "application/pdf"
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext == ".webp":
        return "image/webp"
    return "image/jpeg"


def detect_image_mime_from_header(header: bytes) -> str:
    """
    Detect the image MIME type from the first bytes of a file.

    This replaces the stdlib 'imghdr' module, which was removed in Python 3.13.
    Only the formats this project actually handles are recognised; anything
    else falls back to 'image/jpeg', which is what imghdr.what() effectively
    produced here as well.
    """
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"

    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"

    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"

    if header.startswith(b"GIF87a") or header.startswith(b"GIF89a"):
        return "image/gif"

    if header.startswith(b"BM"):
        return "image/bmp"

    if header.startswith(b"II*\x00") or header.startswith(b"MM\x00*"):
        return "image/tiff"

    return "image/jpeg"


def image_mime_type(path: Path) -> str:
    """Detect MIME type of an image from its content (only for local files, not PDF)."""
    try:
        with open(path, "rb") as image_file:
            header = image_file.read(32)
    except OSError:
        return "image/jpeg"

    return detect_image_mime_from_header(header)


def collect_image_files(
    root_dir: Path,
    exts: Tuple[str, ...],
    config: Dict[str, Any]
) -> List[Path]:
    """Collect image files from local directory."""
    imgs: List[Path] = []

    for subdir, _, files in os.walk(root_dir):
        for fname in files:
            if fname.startswith("."):
                continue
            p = Path(subdir) / fname
            if p.suffix.lower() in exts:
                imgs.append(p)

    return sorted(imgs)


def get_image_base64_and_mime(path: Path, config: Dict[str, Any]) -> Tuple[str, str]:
    """
    Load file from local filesystem and return (base64_string, mime_type).
    MIME type is determined by extension - content sniffing is not used for PDF.
    """
    mime = get_mime_type(path)

    # For local images (not PDF), refine MIME from the file content
    if mime != "application/pdf":
        mime = image_mime_type(path)

    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return b64, mime


def get_image_file_for_upload(path: Path, config: Dict[str, Any]) -> Tuple[str, str]:
    """
    Return local file path for upload to AI provider.
    Returns (file_path, mime_type).
    """
    mime = get_mime_type(path)
    return str(path), mime
