"""Parse a decompressed XNB Texture2D payload -> RGBA -> PNG (stdlib only)."""
from __future__ import annotations

import struct
import sys
import zlib

from lzx_xnb import xnb_decompress


def read_7bit_int(data: bytes, pos: int):
    result = 0
    shift = 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def read_string(data: bytes, pos: int):
    length, pos = read_7bit_int(data, pos)
    s = data[pos:pos + length].decode("utf-8", "replace")
    return s, pos + length


# XNA SurfaceFormat enum (subset)
SURFACE_FORMATS = {0: "Color", 1: "Bgr565", 2: "Bgra5551", 3: "Bgra4444",
                   4: "Dxt1", 5: "Dxt3", 6: "Dxt5"}


def decode_texture(path: str):
    payload, platform, version = xnb_decompress(path)
    pos = 0
    reader_count, pos = read_7bit_int(payload, pos)
    readers = []
    for _ in range(reader_count):
        name, pos = read_string(payload, pos)
        ver = struct.unpack_from("<i", payload, pos)[0]
        pos += 4
        readers.append((name, ver))
    shared_count, pos = read_7bit_int(payload, pos)

    # primary object
    type_id, pos = read_7bit_int(payload, pos)  # 1-based index into readers
    surface_format = struct.unpack_from("<i", payload, pos)[0]
    pos += 4
    width = struct.unpack_from("<I", payload, pos)[0]
    pos += 4
    height = struct.unpack_from("<I", payload, pos)[0]
    pos += 4
    mip_count = struct.unpack_from("<I", payload, pos)[0]
    pos += 4
    data_size = struct.unpack_from("<I", payload, pos)[0]
    pos += 4
    pixel_data = payload[pos:pos + data_size]

    return {
        "readers": readers,
        "surface_format": surface_format,
        "surface_name": SURFACE_FORMATS.get(surface_format, f"#{surface_format}"),
        "width": width,
        "height": height,
        "mip_count": mip_count,
        "data_size": data_size,
        "pixels": pixel_data,
    }


def write_png(path: str, width: int, height: int, rgba: bytes):
    # build raw image data: each row prefixed with filter byte 0
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw.extend(rgba[y * stride:(y + 1) * stride])
    compressed = zlib.compress(bytes(raw), 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", compressed))
        f.write(chunk(b"IEND", b""))


def unpremultiply(rgba: bytes) -> bytes:
    out = bytearray(rgba)
    for i in range(0, len(out), 4):
        a = out[i + 3]
        if a == 0:
            out[i] = out[i + 1] = out[i + 2] = 0
        elif a < 255:
            out[i] = min(255, out[i] * 255 // a)
            out[i + 1] = min(255, out[i + 1] * 255 // a)
            out[i + 2] = min(255, out[i + 2] * 255 // a)
    return bytes(out)


if __name__ == "__main__":
    path = sys.argv[1]
    out_png = sys.argv[2] if len(sys.argv) > 2 else None
    info = decode_texture(path)
    print(f"reader        = {info['readers'][0][0].split(',')[0]}")
    print(f"surface fmt   = {info['surface_name']} ({info['surface_format']})")
    print(f"dimensions    = {info['width']} x {info['height']}")
    print(f"mip count     = {info['mip_count']}")
    print(f"pixel bytes   = {info['data_size']}  (expect {info['width']*info['height']*4})")
    # count non-transparent pixels as a sanity signal
    px = info["pixels"]
    opaque = sum(1 for i in range(3, len(px), 4) if px[i] != 0)
    print(f"opaque pixels = {opaque} / {info['width']*info['height']}")
    if out_png and info["surface_format"] == 0:
        rgba = unpremultiply(info["pixels"])
        write_png(out_png, info["width"], info["height"], rgba)
        print(f"wrote PNG     = {out_png}")
