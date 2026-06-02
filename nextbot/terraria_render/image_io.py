"""Minimal numpy PNG codec (8-bit RGBA only) — zero external deps.

The project ships no Pillow; the render assets are 8-bit RGBA PNGs written by
the extraction tool, so we only need to decode/encode that one variant.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np

_SIG = b"\x89PNG\r\n\x1a\n"
_BPP = 4  # 8-bit RGBA


class UnsupportedPNGError(ValueError):
    """Raised for PNG variants this minimal codec does not decode."""


def _parse_chunks(raw: bytes, path: str) -> tuple[int, int, bytes]:
    """Walk PNG chunks -> (width, height, concatenated IDAT). Validates IHDR."""
    if raw[:8] != _SIG:
        raise UnsupportedPNGError(path)
    pos = 8
    width = height = 0
    idat = bytearray()
    while pos < len(raw):
        (length,) = struct.unpack_from(">I", raw, pos)
        tag = raw[pos + 4:pos + 8]
        data = raw[pos + 8:pos + 8 + length]
        pos += 12 + length  # length + tag + data + crc
        if tag == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack_from(">IIBB", data, 0)
            interlace = data[12]
            if bit_depth != 8 or color_type != 6 or interlace != 0:
                raise UnsupportedPNGError(path)
        elif tag == b"IDAT":
            idat += data
        elif tag == b"IEND":
            break
    return width, height, bytes(idat)


def _unfilter_row(
    ft: int, row: np.ndarray, prev: np.ndarray, stride: int,
) -> np.ndarray:
    """Reverse one PNG scanline filter -> reconstructed int32 row (pre-mask)."""
    if ft == 0:  # None
        return row
    if ft == 2:  # Up (fully vectorizable)
        return row + prev.astype(np.int32)
    # Sub(1) / Average(3) / Paeth(4): horizontal recursion -> per-byte
    recon = np.zeros(stride, dtype=np.int32)
    for i in range(stride):
        a = recon[i - _BPP] if i >= _BPP else 0
        b = int(prev[i])
        c = int(prev[i - _BPP]) if i >= _BPP else 0
        if ft == 1:
            pred = a
        elif ft == 3:
            pred = (a + b) >> 1
        else:  # Paeth
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
        recon[i] = row[i] + pred
    return recon


def read_png(path: str) -> np.ndarray:
    """Decode an 8-bit RGBA, non-interlaced PNG -> (h, w, 4) uint8."""
    raw = Path(path).read_bytes()
    width, height, idat = _parse_chunks(raw, path)

    decompressed = zlib.decompress(idat)
    stride = width * _BPP
    out = np.zeros((height, stride), dtype=np.uint8)
    prev = np.zeros(stride, dtype=np.uint8)
    src = np.frombuffer(decompressed, dtype=np.uint8)
    for y in range(height):
        ft = src[y * (stride + 1)]
        row = src[y * (stride + 1) + 1: y * (stride + 1) + 1 + stride].astype(np.int32)
        recon = _unfilter_row(int(ft), row, prev, stride) & 0xFF
        out[y] = recon.astype(np.uint8)
        prev = out[y]
    return out.reshape(height, width, _BPP)


def write_png(rgba: np.ndarray) -> bytes:
    """Encode (h, w, 4) uint8 -> PNG bytes (filter 0, max compression)."""
    h, w = rgba.shape[0], rgba.shape[1]
    rows = bytearray()
    flat = np.ascontiguousarray(rgba, dtype=np.uint8).reshape(h, w * _BPP)
    for y in range(h):
        rows.append(0)
        rows.extend(flat[y].tobytes())
    comp = zlib.compress(bytes(rows), 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (_SIG
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", comp)
            + chunk(b"IEND", b""))
