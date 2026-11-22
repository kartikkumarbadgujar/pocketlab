import io
import struct
import zipfile


def build_axml(package: str, version_code: int = 42, version_name: str = "1.4.2", min_sdk: int = 24) -> bytes:
    strings = ["versionCode", "versionName", "minSdkVersion", "package", "manifest", "uses-sdk", version_name, package]
    body = b""
    offsets = []
    for value in strings:
        offsets.append(len(body))
        body += struct.pack("<H", len(value)) + value.encode("utf-16-le") + b"\x00\x00"
    body += b"\x00" * (-len(body) % 4)
    header = 28
    pool = struct.pack("<HHIIIIII", 0x0001, header, header + 4 * len(strings) + len(body), len(strings), 0, 0, header + 4 * len(strings), 0)
    pool += struct.pack(f"<{len(strings)}I", *offsets) + body
    resource_map = struct.pack("<HHI3I", 0x0180, 8, 20, 0x0101021B, 0x0101021C, 0x0101020C)

    def element(name_index: int, attributes: list[tuple[int, int, int, int]]) -> bytes:
        payload = struct.pack("<IIHHHHHH", 0xFFFFFFFF, name_index, 20, 20, len(attributes), 0, 0, 0)
        for attribute_name, raw, data_type, value in attributes:
            payload += struct.pack("<IIIHBBI", 0xFFFFFFFF, attribute_name, raw, 8, 0, data_type, value)
        return struct.pack("<HHIII", 0x0102, 16, 16 + len(payload), 1, 0xFFFFFFFF) + payload

    manifest = element(4, [(0, 0xFFFFFFFF, 0x10, version_code), (1, 6, 0x03, 6), (3, 7, 0x03, 7)])
    uses_sdk = element(5, [(2, 0xFFFFFFFF, 0x10, min_sdk)])
    content = pool + resource_map + manifest + uses_sdk
    return struct.pack("<HHI", 0x0003, 8, 8 + len(content)) + content


def valid_apk_bytes(package: str = "com.example.app") -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("AndroidManifest.xml", build_axml(package))
        archive.writestr("classes.dex", b"dex\n035")
    return stream.getvalue()
