"""In-memory screenshot downscale to JPEG (max edge)."""
from __future__ import annotations

import io


def shrink_frame_bytes(
    data: bytes,
    *,
    mime: str = "image/png",
    max_edge: int = 1280,
    quality: int = 80,
) -> tuple[bytes, int, int]:
    """Decode image bytes, downscale so max(w,h) <= max_edge, encode as JPEG."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("pillow required for shrink") from exc

    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        w, h = img.size
        longest = max(w, h)
        if longest > max_edge:
            scale = max_edge / longest
            w = max(1, round(w * scale))
            h = max(1, round(h * scale))
            img = img.resize((w, h), Image.Resampling.LANCZOS)

        out = io.BytesIO()
        img.save(out, format="JPEG", quality=quality)
        return out.getvalue(), w, h
