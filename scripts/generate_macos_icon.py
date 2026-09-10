"""Generate resources/icons/despatch.icns from the packaged product PNG.

Run with the same Envoy build Stack used for packaging (PySide6 provides the
PNG resizing/encoding; no extra dependency needed beyond what the executable
build already requires):

    envoy --stack=tests/fixtures/stacks/build/build.estack python scripts/generate_macos_icon.py

Re-run this whenever the source artwork changes; the resulting .icns is a
committed binary asset (there is no macOS build-time step that generates it).
"""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6 import QtCore, QtGui

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PNG = REPOSITORY_ROOT / "resources" / "icons" / "despatch_icon_charcoal_1024.png"
OUTPUT_ICNS = REPOSITORY_ROOT / "resources" / "icons" / "despatch.icns"

# Modern ("ic??") ICNS entries are just PNG-encoded images tagged with a
# 4-byte OSType code identifying their pixel size. This covers the sizes
# macOS actually requests (Finder/Dock/Spotlight); smaller sizes are
# downscaled by the OS from the closest larger entry, so a minimal set of
# four is sufficient for a clean result at every size macOS will use.
_ICNS_ENTRIES = (
    (b"ic07", 128),
    (b"ic08", 256),
    (b"ic09", 512),
    (b"ic10", 1024),
)


def _encodePng(image: QtGui.QImage) -> bytes:
    """Encode a QImage as PNG bytes."""
    buffer = QtCore.QBuffer()
    if not buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly):
        raise RuntimeError("Could not open QBuffer for PNG encoding")
    if not image.save(buffer, "PNG"):
        raise ValueError("Failed to encode image as PNG")
    return bytes(buffer.data())


def buildIcns(source_png: Path) -> bytes:
    """Build ICNS container bytes from a single square source PNG.

    Args:
        source_png: Path to a square, high-resolution source image.

    Returns:
        Complete ICNS file contents.

    Raises:
        ValueError: If the source image can't be loaded or isn't square.

    """
    source_image = QtGui.QImage(str(source_png))
    if source_image.isNull():
        raise ValueError(f"Could not load source image: {source_png}")
    if source_image.width() != source_image.height():
        raise ValueError(f"Source image must be square: {source_png}")

    entries = bytearray()
    for type_code, size in _ICNS_ENTRIES:
        scaled = source_image.scaled(
            size,
            size,
            QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        png_bytes = _encodePng(scaled)
        entries += type_code
        entries += struct.pack(">I", len(png_bytes) + 8)
        entries += png_bytes

    header = b"icns" + struct.pack(">I", len(entries) + 8)
    return header + bytes(entries)


def main() -> int:
    """Generate the macOS icon asset and report its size."""
    icns_bytes = buildIcns(SOURCE_PNG)
    OUTPUT_ICNS.write_bytes(icns_bytes)
    print(f"Wrote {OUTPUT_ICNS} ({len(icns_bytes)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
