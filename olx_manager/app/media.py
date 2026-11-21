import hashlib
import io
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from threading import BoundedSemaphore

from PIL import Image, ImageFile, ImageOps, UnidentifiedImageError

MAX_BYTES = 12 * 1024 * 1024
MAX_IMAGES = 20
MAX_PIXELS = 30_000_000
MAX_DIMENSION = 9000
FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
ImageFile.LOAD_TRUNCATED_IMAGES = False
DECODERS = BoundedSemaphore(2)


class DomainError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.status = status


class ConflictError(DomainError):
    def __init__(self, message):
        super().__init__("REVISION_CONFLICT", message, 409)


@dataclass(frozen=True)
class Photo:
    sha256: str
    original: bytes
    mime: str
    width: int
    height: int
    thumbnail: bytes


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def decode(raw, claimed_mime=None):
    if not 1 <= len(raw) <= MAX_BYTES:
        raise DomainError("IMAGE_SIZE", "Maksymalny rozmiar zdjęcia to 12 MB.", 413)
    try:
        with DECODERS, Image.open(io.BytesIO(raw), formats=list(FORMATS)) as src:
            mime = FORMATS[src.format]
            if claimed_mime and claimed_mime not in {mime, "application/octet-stream"}:
                raise DomainError("IMAGE_MIME", "Typ pliku różni się od zawartości.", 415)
            if (
                max(src.size) > MAX_DIMENSION
                or src.width * src.height > MAX_PIXELS
                or getattr(src, "is_animated", False)
            ):
                raise DomainError("IMAGE_DIMENSIONS", "Limit to 30 MP, 9000 px na bok i jedna klatka.", 415)
            src.verify()
        with DECODERS, Image.open(io.BytesIO(raw), formats=list(FORMATS)) as src:
            src.load()
            img = ImageOps.exif_transpose(src)
            width, height = img.size
            img.thumbnail((900, 900))
            # Re-encode the preview without metadata, preserving the original separately.
            thumb = io.BytesIO()
            img.convert("RGB").save(thumb, "JPEG", quality=85)
            return Photo(sha256(raw), raw, mime, width, height, thumb.getvalue())
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        OSError,
        SyntaxError,
        EOFError,
        OverflowError,
    ) as err:
        raise DomainError("INVALID_IMAGE", "Wybierz poprawny plik JPG, PNG lub WebP.", 415) from err


def read_from(directory: Path, *parts: str):
    if any(not p or p in (".", "..") or "/" in p or "\\" in p or "\0" in p for p in parts):
        raise DomainError("INVALID_PATH", "Podaj samą nazwę pliku.")
    try:
        directory_parts = Path(os.path.abspath(directory)).parts
        fd = os.open(directory_parts[0], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in (*directory_parts[1:], *parts[:-1]):
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(file_fd, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise DomainError("INVALID_PATH", "Dozwolone są zwykłe pliki.")
                if info.st_size > MAX_BYTES:
                    raise DomainError("IMAGE_SIZE", "Maksymalny rozmiar zdjęcia to 12 MB.", 413)
                return source.read(MAX_BYTES + 1)
        finally:
            os.close(fd)
    except OSError as err:
        raise DomainError(
            "INBOX_FILE_UNAVAILABLE", "Plik nie istnieje lub ścieżka jest niedozwolona.", 404
        ) from err


def rendered(photo, publication=False):
    if not publication and not photo["rotation"]:
        return photo["data"]
    with DECODERS, Image.open(io.BytesIO(photo["data"])) as src:
        src.load()
        img = ImageOps.exif_transpose(src).rotate(-photo["rotation"], expand=True)
        if publication:
            img.thumbnail((2400, 2400))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=90 if publication else 85)
        return buf.getvalue()
