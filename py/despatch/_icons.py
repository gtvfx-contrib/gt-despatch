"""Qt icon helpers for packaged and manifest resources."""

from __future__ import annotations

import base64
import binascii
from importlib import resources
from pathlib import Path
from xml.etree import ElementTree

from Qt import QtGui

_ICON_CACHE: dict[str, QtGui.QIcon] = {}


def loadProductIcon() -> QtGui.QIcon:
    """Return the packaged Despatch product icon."""
    return loadPackagedIcon("despatch_icon_charcoal_1024.png")


def loadRefreshingIcon() -> QtGui.QIcon:
    """Return the packaged Despatch icon used while a catalog refresh runs.

    Note:
        Reuses the existing orange product-icon asset -- shipped alongside
        the default charcoal one but previously unused anywhere -- as a
        distinct, already-designed "busy" variant rather than introducing
        new artwork.

    """
    return loadPackagedIcon("despatch_icon_orange_1024.png")


def loadPackagedIcon(icon_name: str) -> QtGui.QIcon:
    """Load and cache an icon bundled with Despatch.

    Args:
        icon_name: Filename below the packaged icons directory.

    Returns:
        Loaded icon, or an empty icon when unavailable.

    """
    cache_key = f"package:{icon_name}"
    if cache_key in _ICON_CACHE:
        return _ICON_CACHE[cache_key]
    icon_path = _findResourceIcon(icon_name)
    icon = QtGui.QIcon(str(icon_path)) if icon_path is not None else QtGui.QIcon()
    _ICON_CACHE[cache_key] = icon
    return icon


def _findResourceIcon(icon_name: str) -> Path | None:
    """Resolve an icon from the repository or installed resource package.

    Args:
        icon_name: Filename below the resources icon directory.

    Returns:
        Resolved filesystem path, or None when the icon is unavailable.

    """
    repository_path = Path(__file__).resolve().parents[2] / "resources" / "icons" / icon_name
    if repository_path.is_file():
        return repository_path
    packaged_path = Path(__file__).resolve().parent / "resources" / "icons" / icon_name
    if packaged_path.is_file():
        return packaged_path
    try:
        icon_resource = resources.files("despatch.resources").joinpath("icons", icon_name)
    except (ModuleNotFoundError, TypeError):
        return None
    if icon_resource.is_file():
        return Path(str(icon_resource))
    return None


def loadPathIcon(icon_path: Path | None) -> QtGui.QIcon:
    """Load and cache a manifest icon path.

    Args:
        icon_path: Validated icon path, or None.

    Returns:
        Loaded icon, falling back to the product icon.

    """
    if icon_path is None:
        return loadProductIcon()
    cache_key = str(icon_path)
    if cache_key not in _ICON_CACHE:
        _ICON_CACHE[cache_key] = _loadFileIcon(icon_path)
    return _ICON_CACHE[cache_key]


def _loadFileIcon(icon_path: Path) -> QtGui.QIcon:
    """Load an icon with a fallback for SVG files containing embedded images."""
    if icon_path.suffix.casefold() == ".svg":
        embedded_icon = _loadEmbeddedImageIcon(icon_path)
        if embedded_icon is not None:
            return embedded_icon
    return QtGui.QIcon(str(icon_path))


def _loadEmbeddedImageIcon(icon_path: Path) -> QtGui.QIcon | None:
    """Extract a raster image from an SVG that Qt may not render correctly."""
    try:
        svg_root = ElementTree.parse(icon_path).getroot()
    except (ElementTree.ParseError, OSError):
        return None
    for element in svg_root.iter():
        if element.tag.rsplit("}", 1)[-1] != "image":
            continue
        image_reference = next(
            (
                value
                for attribute_name, value in element.attrib.items()
                if attribute_name.rsplit("}", 1)[-1] == "href"
            ),
            "",
        )
        header, separator, encoded_image = image_reference.partition(",")
        if (
            not separator
            or not header.casefold().startswith("data:image/")
            or ";base64" not in header.casefold()
        ):
            continue
        try:
            image_data = base64.b64decode("".join(encoded_image.split()), validate=True)
        except (binascii.Error, ValueError):
            continue
        pixmap = QtGui.QPixmap()
        if pixmap.loadFromData(image_data):
            return QtGui.QIcon(_extractLeadingMark(pixmap))
    return None


def _extractLeadingMark(pixmap: QtGui.QPixmap) -> QtGui.QPixmap:
    """Extract a square leading mark from a wide transparent wordmark image."""
    if pixmap.width() < pixmap.height() * 2:
        return pixmap
    image = pixmap.toImage()
    minimum_gap = max(3, image.height() // 12)
    occupied_columns = [
        any(image.pixelColor(column, row).alpha() > 8 for row in range(image.height()))
        for column in range(image.width())
    ]
    try:
        left = occupied_columns.index(True)
    except ValueError:
        return pixmap
    empty_columns = 0
    right = min(image.width(), left + round(image.height() * 1.35))
    for column in range(left, image.width()):
        if occupied_columns[column]:
            empty_columns = 0
            continue
        empty_columns += 1
        if empty_columns >= minimum_gap:
            right = column - empty_columns + 1
            break
    occupied_rows = [
        any(image.pixelColor(column, row).alpha() > 8 for column in range(left, right))
        for row in range(image.height())
    ]
    try:
        top = occupied_rows.index(True)
        bottom = len(occupied_rows) - occupied_rows[::-1].index(True)
    except ValueError:
        return pixmap
    cropped = pixmap.copy(left, top, right - left, bottom - top)
    padding = max(2, round(max(cropped.width(), cropped.height()) * 0.08))
    side = max(cropped.width(), cropped.height()) + padding * 2
    square_pixmap = QtGui.QPixmap(side, side)
    square_pixmap.fill(QtGui.QColor(0, 0, 0, 0))
    painter = QtGui.QPainter(square_pixmap)
    painter.drawPixmap(
        (side - cropped.width()) // 2,
        (side - cropped.height()) // 2,
        cropped,
    )
    painter.end()
    return square_pixmap


def clearCache() -> None:
    """Clear all cached icons."""
    _ICON_CACHE.clear()
