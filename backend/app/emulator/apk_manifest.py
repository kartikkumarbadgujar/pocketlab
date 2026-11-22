"""Minimal, dependency-free parser for the binary AndroidManifest.xml (AXML) inside an APK.

Extracts package name, version and SDK levels. Attribute names are resolved through the resource map as
well as the string pool, so manifests with obfuscated/stripped attribute names still parse. Bounds-checked:
malformed input raises ApkValidationError instead of reading out of range.
"""
from __future__ import annotations

import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app.emulator.exceptions import ApkValidationError

RES_XML_TYPE = 0x0003
RES_STRING_POOL_TYPE = 0x0001
RES_XML_RESOURCE_MAP_TYPE = 0x0180
RES_XML_START_ELEMENT_TYPE = 0x0102
NO_INDEX = 0xFFFFFFFF
ANDROID_ATTRS = {0x0101021B: "versionCode", 0x0101021C: "versionName", 0x0101020C: "minSdkVersion",
                 0x01010270: "targetSdkVersion", 0x0101000F: "debuggable", 0x01010272: "testOnly"}


@dataclass
class ManifestInfo:
    package: str
    version_code: int | None = None
    version_name: str | None = None
    min_sdk: int | None = None
    target_sdk: int | None = None
    debuggable: bool | None = None
    test_only: bool | None = None
    method: str = "builtin_axml"
    extras: dict = field(default_factory=dict)


def _u16(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def _string_pool(data: bytes, off: int) -> list[str]:
    header_size = _u16(data, off + 2)
    count, _styles, flags, strings_start = struct.unpack_from("<IIII", data, off + 8)
    if count > 200_000:
        raise ApkValidationError("Manifest string pool too large")
    utf8 = bool(flags & 0x100)
    offsets = struct.unpack_from(f"<{count}I", data, off + header_size)
    base = off + strings_start
    strings = []
    for o in offsets:
        p = base + o
        if utf8:
            for _ in range(2):  # utf-16 length, then utf-8 byte length
                n = data[p]
                if n & 0x80:
                    n = ((n & 0x7F) << 8) | data[p + 1]
                    p += 2
                else:
                    p += 1
            strings.append(data[p:p + n].decode("utf-8", "replace"))
        else:
            n = _u16(data, p)
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | _u16(data, p + 2)
                p += 4
            else:
                p += 2
            strings.append(data[p:p + n * 2].decode("utf-16-le", "replace"))
    return strings


def parse_axml(data: bytes) -> list[tuple[str, dict[str, object]]]:
    if len(data) < 8 or _u16(data, 0) != RES_XML_TYPE:
        raise ApkValidationError("AndroidManifest.xml is not binary AXML")
    strings: list[str] = []
    resource_ids: list[int] = []
    elements: list[tuple[str, dict[str, object]]] = []
    off = _u16(data, 2)
    try:
        while off + 8 <= len(data):
            ctype, hsize, size = _u16(data, off), _u16(data, off + 2), _u32(data, off + 4)
            if size < 8 or off + size > len(data):
                break
            if ctype == RES_STRING_POOL_TYPE:
                strings = _string_pool(data, off)
            elif ctype == RES_XML_RESOURCE_MAP_TYPE:
                resource_ids = list(struct.unpack_from(f"<{(size - hsize) // 4}I", data, off + hsize))
            elif ctype == RES_XML_START_ELEMENT_TYPE:
                body = off + hsize
                name_idx = _u32(data, body + 4)
                attr_start, attr_size, attr_count = _u16(data, body + 8), _u16(data, body + 10), _u16(data, body + 12)
                attrs: dict[str, object] = {}
                for i in range(attr_count):
                    a = body + attr_start + i * attr_size
                    a_name, raw, dtype, value = _u32(data, a + 4), _u32(data, a + 8), data[a + 15], _u32(data, a + 16)
                    key = ANDROID_ATTRS.get(resource_ids[a_name]) if a_name < len(resource_ids) else None
                    key = key or (strings[a_name] if a_name < len(strings) else f"attr_{a_name}")
                    if raw != NO_INDEX and raw < len(strings):
                        attrs[key] = strings[raw]
                    elif dtype == 0x03 and value < len(strings):
                        attrs[key] = strings[value]
                    elif dtype in (0x10, 0x11):
                        attrs[key] = value - (1 << 32) if value >= 1 << 31 else value
                    elif dtype == 0x12:
                        attrs[key] = value != 0
                    else:
                        attrs[key] = value
                elements.append((strings[name_idx] if name_idx < len(strings) else "", attrs))
            off += size
    except (struct.error, IndexError) as exc:
        raise ApkValidationError(f"Malformed binary manifest: {exc}") from exc
    return elements


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def manifest_from_apk(apk: Path) -> ManifestInfo:
    try:
        with zipfile.ZipFile(apk) as archive:
            info = archive.getinfo("AndroidManifest.xml")
            if info.file_size > 10 * 1024 * 1024:
                raise ApkValidationError("Manifest exceeds safe size")
            data = archive.read(info)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ApkValidationError(f"APK archive invalid: {exc}") from exc
    elements = parse_axml(data)
    manifest = next((attrs for tag, attrs in elements if tag == "manifest"), None)
    if not manifest or not manifest.get("package"):
        raise ApkValidationError("Manifest has no package attribute")
    sdk = next((attrs for tag, attrs in elements if tag == "uses-sdk"), {})
    app = next((attrs for tag, attrs in elements if tag == "application"), {})
    return ManifestInfo(package=str(manifest["package"]), version_code=_as_int(manifest.get("versionCode")),
                        version_name=str(manifest["versionName"]) if manifest.get("versionName") is not None else None,
                        min_sdk=_as_int(sdk.get("minSdkVersion")) or 1, target_sdk=_as_int(sdk.get("targetSdkVersion")),
                        debuggable=app.get("debuggable") if isinstance(app.get("debuggable"), bool) else None,
                        test_only=app.get("testOnly") if isinstance(app.get("testOnly"), bool) else None)


def parse_aapt2_badging(text: str) -> ManifestInfo:
    import re
    pkg = re.search(r"package: name='([^']+)'(?: versionCode='(\d+)')?(?: versionName='([^']*)')?", text)
    if not pkg:
        raise ApkValidationError("aapt2 output did not contain a package line")
    min_sdk = re.search(r"(?:minSdkVersion|sdkVersion):'(\d+)'", text)
    target = re.search(r"targetSdkVersion:'(\d+)'", text)
    return ManifestInfo(package=pkg.group(1), version_code=_as_int(pkg.group(2)), version_name=pkg.group(3),
                        min_sdk=_as_int(min_sdk.group(1)) if min_sdk else 1, target_sdk=_as_int(target.group(1)) if target else None,
                        debuggable="application-debuggable" in text, test_only="testOnly='-1'" in text, method="aapt2 dump badging")
