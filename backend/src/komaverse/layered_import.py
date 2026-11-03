"""Layered-raster + animation import: OpenRaster (.ora) and GIF/APNG.

.ora is the interchange format Krita, GIMP, and MyPaint all export: a
zip holding full-canvas metadata (stack.xml), one PNG per layer, and
a merged preview. GIF/APNG split into cel frames with Pillow. Both
land as draw frames with intact layer stacks (see api.animate).
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from PIL import Image

MAX_IMPORT_LAYERS = 64
MAX_IMPORT_DIM = 4096
MAX_IMPORT_FRAMES = 48

# stack.xml composite-op -> canvas blend mode (BLEND_MODES in api.animate).
_COMPOSITE_OPS = {
    "svg:src-over": "normal",
    "svg:multiply": "multiply",
    "svg:screen": "screen",
    "svg:overlay": "overlay",
    "svg:darken": "darken",
    "svg:lighten": "lighten",
    "svg:color-dodge": "color-dodge",
    "svg:color-burn": "color-burn",
    "svg:hard-light": "hard-light",
    "svg:soft-light": "soft-light",
    "svg:difference": "difference",
}

_NS = {
    "image": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
}


@dataclass
class ImportLayer:
    name: str
    opacity: int  # 0..100
    visible: bool
    blend_mode: str
    png: bytes  # full-canvas RGBA


@dataclass
class OraDocument:
    width: int
    height: int
    layers: list[ImportLayer] = field(default_factory=list)  # bottom-first


class LayeredImportError(ValueError):
    """Bad file, told to the user as a 400."""


def _png_rgba(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGBA").save(buf, format="PNG")
    return buf.getvalue()


def parse_ora(data: bytes) -> OraDocument:
    """Parse an OpenRaster file into full-canvas RGBA layers, bottom-first.

    Layer PNGs carry their own (x, y) offsets in stack.xml and are
    usually smaller than the canvas, so each is pasted onto a
    transparent sheet — LayerRow has no offset field.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception as exc:
        raise LayeredImportError("not a readable .ora file") from exc
    try:
        names = set(zf.namelist())
        if "stack.xml" not in names and "Stack.xml" not in names:
            raise LayeredImportError(".ora is missing stack.xml")
        stack_name = "stack.xml" if "stack.xml" in names else "Stack.xml"
        try:
            root = ET.fromstring(zf.read(stack_name))
        except Exception as exc:
            raise LayeredImportError("broken stack.xml") from exc
        # Per the OpenRaster spec the document root itself is <image>.
        image_el = root if root.tag == "image" else root.find("image")
        if image_el is None:
            raise LayeredImportError("stack.xml has no <image>")
        try:
            width = int(image_el.get("w", "0"))
            height = int(image_el.get("h", "0"))
        except ValueError as exc:
            raise LayeredImportError("bad canvas size in stack.xml") from exc
        if width <= 0 or height <= 0:
            raise LayeredImportError("bad canvas size in stack.xml")
        if max(width, height) > MAX_IMPORT_DIM:
            scale = MAX_IMPORT_DIM / max(width, height)
            width, height = max(8, round(width * scale)), max(8, round(height * scale))
            rescale = scale
        else:
            rescale = 1.0
        stack_el = image_el.find("stack")
        if stack_el is None:
            raise LayeredImportError("stack.xml has no layer stack")
        raw_layers = list(stack_el.iter("layer"))
        if not raw_layers:
            raise LayeredImportError(".ora holds no layers")
        if len(raw_layers) > MAX_IMPORT_LAYERS:
            raise LayeredImportError(f"too many layers ({len(raw_layers)} > {MAX_IMPORT_LAYERS})")
        # stack.xml lists top-first; the studio stacks bottom-first.
        layers: list[ImportLayer] = []
        for el in reversed(raw_layers):
            src = el.get("src")
            if not src:
                raise LayeredImportError("a layer is missing its image")
            src = src.lstrip("/")
            if src not in names:
                raise LayeredImportError(f"missing layer image {src}")
            try:
                with zf.open(src) as fh:
                    img = Image.open(io.BytesIO(fh.read()))
                    img.load()
            except Exception as exc:
                raise LayeredImportError(f"cannot decode {src}") from exc
            try:
                ox, oy = int(el.get("x", "0")), int(el.get("y", "0"))
            except ValueError as exc:
                raise LayeredImportError("bad layer offset in stack.xml") from exc
            try:
                opacity = round(float(el.get("opacity", "1.0")) * 100)
            except ValueError:
                opacity = 100
            sheet = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            if rescale != 1.0:
                img = img.resize(
                    (max(1, round(img.width * rescale)), max(1, round(img.height * rescale))),
                    Image.LANCZOS,
                )
                ox, oy = round(ox * rescale), round(oy * rescale)
            sheet.alpha_composite(img.convert("RGBA"), (ox, oy))
            layers.append(
                ImportLayer(
                    name=(el.get("name") or "Layer")[:200],
                    opacity=min(max(opacity, 0), 100),
                    visible=(el.get("visibility") or "visible") != "hidden",
                    blend_mode=_COMPOSITE_OPS.get(el.get("composite-op") or "svg:src-over", "normal"),
                    png=_png_rgba(sheet),
                )
            )
        return OraDocument(width=width, height=height, layers=layers)
    finally:
        zf.close()


def split_animation(data: bytes) -> tuple[int, int, list[bytes]]:
    """Split GIF/APNG bytes into full-canvas RGBA cel PNGs, in order."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        raise LayeredImportError("cannot decode animation") from exc
    if (img.format or "").upper() not in ("GIF", "PNG"):
        raise LayeredImportError("not a GIF or animated PNG")
    n = getattr(img, "n_frames", 1)
    if n < 1:
        raise LayeredImportError("animation holds no frames")
    frames: list[bytes] = []
    for i in range(min(n, MAX_IMPORT_FRAMES)):
        try:
            img.seek(i)
        except EOFError:
            break
        canvas = Image.new("RGBA", img.size, (0, 0, 0, 0))
        # Pasted frame, not composited: GIF disposal already applies in
        # the decoded frame; alpha_composite would double-blend it.
        canvas.paste(img.convert("RGBA"), (0, 0))
        frames.append(_png_rgba(canvas))
    if not frames:
        raise LayeredImportError("animation holds no frames")
    w, h = img.size
    if max(w, h) > MAX_IMPORT_DIM:
        scale = MAX_IMPORT_DIM / max(w, h)
        w, h = max(8, round(w * scale)), max(8, round(h * scale))
        out = []
        for png in frames:
            f = Image.open(io.BytesIO(png)).resize((w, h), Image.LANCZOS)
            out.append(_png_rgba(f))
        frames = out
    return w, h, frames
