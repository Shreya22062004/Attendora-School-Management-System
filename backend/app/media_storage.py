"""Cloudinary-backed storage for ID-card media (backend credentials only)."""
from io import BytesIO
import os
from urllib.request import urlopen

import cloudinary
import cloudinary.uploader
import cloudinary.utils
from PIL import Image, ImageOps


class MediaStorageError(RuntimeError):
    pass


def _configure():
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
    api_key = os.getenv("CLOUDINARY_API_KEY")
    api_secret = os.getenv("CLOUDINARY_API_SECRET")
    if not all((cloud_name, api_key, api_secret)):
        raise MediaStorageError("Cloudinary is not configured")
    cloudinary.config(cloud_name=cloud_name, api_key=api_key, api_secret=api_secret, secure=True)


def put_bytes(public_id: str, data: bytes, mime_type: str) -> tuple[str, str]:
    try:
        _configure()
        result = cloudinary.uploader.upload(
            BytesIO(data), resource_type="image", public_id=public_id,
            overwrite=True, invalidate=True,
            format="jpg" if mime_type == "image/jpeg" else "png",
        )
        if not result.get("public_id") or not result.get("secure_url"):
            raise MediaStorageError("Cloudinary did not return a media reference")
        return result["public_id"], result["secure_url"]
    except MediaStorageError:
        raise
    except Exception as error:
        raise MediaStorageError("Could not save media to Cloudinary") from error


def get_bytes(public_id: str, secure_url: str | None = None) -> tuple[bytes, str]:
    try:
        _configure()
        url = secure_url
        if not url:
            url, _ = cloudinary.utils.cloudinary_url(public_id, resource_type="image", secure=True)
        with urlopen(url, timeout=20) as response:
            return response.read(), response.headers.get_content_type() or "image/jpeg"
    except Exception as error:
        raise MediaStorageError("Could not retrieve media from Cloudinary") from error


def delete(public_id: str):
    try:
        _configure()
        cloudinary.uploader.destroy(public_id, resource_type="image", invalidate=True)
    except Exception as error:
        raise MediaStorageError("Could not remove media from Cloudinary") from error


def optimize_student_photo(data: bytes) -> tuple[bytes, str]:
    """Create a compact, upright JPEG suitable for the current ID-card frame."""
    try:
        image = ImageOps.exif_transpose(Image.open(BytesIO(data))).convert("RGB")
        image.thumbnail((800, 800), Image.Resampling.LANCZOS)
        output = BytesIO()
        image.save(output, format="JPEG", quality=85, optimize=True, progressive=True)
        return output.getvalue(), "image/jpeg"
    except Exception as error:
        raise MediaStorageError("Could not optimize student photo") from error
