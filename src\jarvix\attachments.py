"""Explicit local image selection, bounded decoding and per-request disclosure.

Images never enter history automatically. Pixels are re-encoded without EXIF,
GPS or source filenames in provider payloads; the approved bytes are immutable.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from jarvix.domain import ImageAttachment, PermissionRequest, ProviderError
from jarvix.runtime import check_cancelled

MAX_IMAGES = 4
MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_ENCODED_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 32_000_000


def prepare_images(paths) -> list[ImageAttachment]:
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice
    from PySide6.QtGui import QImageReader
    from jarvix.capabilities.files import _linked
    from jarvix.tools.builtin import _is_sensitive

    if not isinstance(paths, (tuple, list)) or len(paths) > MAX_IMAGES:
        raise ValueError("Attach at most four images per request.")
    prepared, total = [], 0
    for value in paths:
        check_cancelled()
        path = Path(value)
        if (not path.is_absolute() or _is_sensitive(path)
                or any(_linked(part) for part in (path, *path.parents))):
            raise ValueError("Select an ordinary local image outside protected paths.")
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("Each image must be a regular file no larger than 12 MB.")
        # Read once; the approval and provider use this snapshot even if the
        # selected file is subsequently changed by another application.
        with path.open("rb") as source:
            raw = source.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError("The selected image grew beyond the local size limit.")
        data = QByteArray(raw)
        source_buffer = QBuffer(data)
        source_buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(source_buffer)
        reader.setAutoTransform(True)
        size = reader.size()
        if (bytes(reader.format()).lower() not in {b"png", b"jpeg", b"jpg", b"webp", b"bmp"}
                or size.width() <= 0 or size.height() <= 0
                or size.width() * size.height() > MAX_PIXELS):
            raise ValueError("Use a valid PNG, JPEG, WebP or BMP image under 32 megapixels.")
        pixels = reader.read()
        if pixels.isNull():
            raise ValueError("The selected image could not be decoded.")
        # Render into a fresh image to discard text chunks / EXIF metadata.
        from PySide6.QtGui import QImage, QPainter
        clean = QImage(pixels.size(), QImage.Format.Format_ARGB32)
        clean.fill(0)
        painter = QPainter(clean)
        painter.drawImage(0, 0, pixels)
        painter.end()
        output = QByteArray()
        sink = QBuffer(output)
        sink.open(QIODevice.OpenModeFlag.WriteOnly)
        if not clean.save(sink, "PNG"):
            raise ValueError("Could not prepare the selected image.")
        image_bytes = bytes(output)
        encoded = base64.b64encode(image_bytes).decode("ascii")
        total += len(encoded)
        if total > MAX_ENCODED_BYTES:
            raise ValueError("Attached images exceed 16 MB after preparation. Use smaller images.")
        prepared.append(ImageAttachment(path.name, "image/png", encoded,
                                        hashlib.sha256(image_bytes).hexdigest(), clean.width(), clean.height()))
    return prepared


def disclose_images(paths, provider, model, approve, repository) -> list[ImageAttachment]:
    if not paths:
        return []
    if not getattr(provider, "supports_vision", False):
        raise ProviderError("This provider does not support image attachments.")
    images = prepare_images(paths)
    description = {"provider": provider.id, "model": model,
                   "images": [image.description() for image in images],
                   "scope": "This chat request, including its bounded tool follow-ups. Not future requests."}
    request = PermissionRequest("disclose", "chat.images", "cloud.vision",
                                "Send these image pixels to the selected cloud AI provider? Inspect each image for private information first. Local OCR remains available without this upload.",
                                description, json.dumps(description, indent=2), tuple(images))
    approved = bool(approve(request))
    repository.audit("permission", f"chat.images: {'approved' if approved else 'denied'}")
    check_cancelled()
    if not approved:
        raise ProviderError("Image sharing was denied. No image or prompt was sent to the provider.")
    return images


def context_message(message):
    """Inspector manifest without binary content or provider-private signatures."""
    from dataclasses import asdict
    value = {"role": message.role, "content": message.content,
             "tool_calls": [asdict(call) for call in message.tool_calls],
             "tool_call_id": message.tool_call_id, "name": message.name,
             "metadata": {key: val for key, val in message.metadata.items()
                          if key not in {"gemini_parts", "gemini_call_ids"}}}
    if message.images:
        value["images"] = [image.description() for image in message.images]
    return value
