"""
Cloudflare R2 Image Storage Adapter
Handles image storage and retrieval from Cloudflare R2.
"""
import base64
import tempfile
import requests
from urllib.parse import quote
from pathlib import Path
from typing import List, Tuple, Dict, Any
import boto3


def get_r2_client(config: Dict[str, Any]):
    """Create and return Cloudflare R2 S3 client."""
    image_config = config.get('image_management', {})
    cloudflare_config = image_config.get('cloudflare', {})
    return boto3.client(
        "s3",
        endpoint_url=cloudflare_config.get('r2_endpoint_url'),
        aws_access_key_id=cloudflare_config.get('r2_access_key_id'),
        aws_secret_access_key=cloudflare_config.get('r2_secret_access_key'),
        region_name="auto",
    )


def get_mime_type(path: Path) -> str:
    """
    Return correct MIME type based on file extension.
    For R2 objects only the key (extension) is available, so the MIME type is
    always derived from it - the content is never sniffed here.
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


# def collect_image_files(
#     root_dir: Path,
#     exts: Tuple[str, ...],
#     config: Dict[str, Any]
# ) -> List[Path]:
#     """Collect image files from Cloudflare R2."""
#     image_config = config.get('image_management', {})
#     cloudflare_config = image_config.get('cloudflare', {})
    
#     imgs: List[Path] = []
#     s3 = get_r2_client(config)
#     paginator = s3.get_paginator("list_objects_v2")
#     bucket_name = cloudflare_config.get('r2_bucket_name')

#     for page in paginator.paginate(Bucket=bucket_name):
#         for obj in page.get("Contents", []):
#             key = obj["Key"]
#             if key.lower().endswith(exts):
#                 imgs.append(Path(key))

#     return sorted(imgs)

def collect_image_files(
    root_dir: Path,
    exts: Tuple[str, ...],
    config: Dict[str, Any]
) -> List[Path]:
    """Collect image files from Cloudflare R2."""
    image_config = config.get('image_management', {})
    cloudflare_config = image_config.get('cloudflare', {})
    
    imgs: List[Path] = []
    s3 = get_r2_client(config)
    paginator = s3.get_paginator("list_objects_v2")
    bucket_name = cloudflare_config.get('r2_bucket_name')
    
    # Získání definované cesty pro Cloudflare (defaultně prázdné = root)
    r2_base_path = cloudflare_config.get('r2_base_path', '')

    # Příprava argumentů pro S3 dotaz
    paginate_kwargs = {"Bucket": bucket_name}
    
    if r2_base_path:
        # S3/R2 klíče obvykle nezačínají lomítkem, proto ho odstraníme
        r2_base_path = r2_base_path.lstrip('/')
        # Abychom hledali přesně ve složce (a nehledali např. složku "batch_stara"),
        # zajistíme, že cesta končí lomítkem.
        if r2_base_path and not r2_base_path.endswith('/'):
            r2_base_path += '/'
            
        paginate_kwargs["Prefix"] = r2_base_path

    # Paginátor nyní přijímá i prefix, pokud byl definován
    for page in paginator.paginate(**paginate_kwargs):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.lower().endswith(exts):
                imgs.append(Path(key))

    return sorted(imgs)


def get_image_base64_and_mime(path: Path, config: Dict[str, Any]) -> Tuple[str, str]:
    """
    Load file from Cloudflare R2 and return (base64_string, mime_type).
    MIME type is determined by extension.
    """
    image_config = config.get('image_management', {})
    cloudflare_config = image_config.get('cloudflare', {})
    public_base_url = cloudflare_config.get('public_base_url', '')
    
    mime = get_mime_type(path)
    url = f"{public_base_url}/{quote(path.as_posix())}"
    response = requests.get(url)
    response.raise_for_status()
    raw = response.content

    b64 = base64.b64encode(raw).decode("ascii")
    return b64, mime


def get_image_file_for_upload(path: Path, config: Dict[str, Any]) -> Tuple[str, str]:
    """
    Download file from Cloudflare R2 to temporary location for upload to AI provider.
    Returns (temp_file_path, mime_type).
    """
    image_config = config.get('image_management', {})
    cloudflare_config = image_config.get('cloudflare', {})
    public_base_url = cloudflare_config.get('public_base_url', '')
    
    mime = get_mime_type(path)
    url = f"{public_base_url}/{quote(path.as_posix())}"
    response = requests.get(url)
    response.raise_for_status()

    ext = path.suffix.lower()

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(response.content)
        tmp_path = tmp.name

    return tmp_path, mime
