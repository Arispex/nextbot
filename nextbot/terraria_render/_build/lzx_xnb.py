"""Self-contained XNB (LZX-compressed) -> raw texture decoder.

Pure Python + stdlib only (zlib for PNG output). No external deps.
Port of the canonical cabextract/MonoGame LZX decoder, plus an XNB
header/Texture2D reader. This is a FEASIBILITY PROBE: prove that the
Terraria .xnb player textures can be turned into RGBA pixels locally.
"""
from __future__ import annotations

import os
import struct
import sys

DEBUG = bool(os.environ.get("LZX_DEBUG"))

# ── LZX decoder ────────────────────────────────────────────────────
MIN_MATCH = 2
NUM_CHARS = 256
BLOCKTYPE_VERBATIM = 1
BLOCKTYPE_ALIGNED = 2
BLOCKTYPE_UNCOMPRESSED = 3
PRETREE_NUM_ELEMENTS = 20
ALIGNED_NUM_ELEMENTS = 8
NUM_PRIMARY_LENGTHS = 7
NUM_SECONDARY_LENGTHS = 249

PRETREE_MAXSYMBOLS = PRETREE_NUM_ELEMENTS
PRETREE_TABLEBITS = 6
MAINTREE_MAXSYMBOLS = NUM_CHARS + 50 * 8
MAINTREE_TABLEBITS = 12
LENGTH_MAXSYMBOLS = NUM_SECONDARY_LENGTHS + 1
LENGTH_TABLEBITS = 12
ALIGNED_MAXSYMBOLS = ALIGNED_NUM_ELEMENTS
ALIGNED_TABLEBITS = 7


class LzxDecoder:
    def __init__(self, window_bits: int):
        wndsize = 1 << window_bits
        self.window_size = wndsize
        self.window = bytearray(wndsize)
        for i in range(wndsize):
            self.window[i] = 0xDC
        self.window_posn = 0

        # position slots
        if window_bits == 20:
            posn_slots = 42
        elif window_bits == 21:
            posn_slots = 50
        else:
            posn_slots = window_bits << 1
        self.main_elements = NUM_CHARS + (posn_slots << 3)

        # extra_bits / position_base tables
        self.extra_bits = [0] * 52
        j = 0
        for i in range(0, 52, 2):
            self.extra_bits[i] = j
            if i + 1 < 52:
                self.extra_bits[i + 1] = j
            if i != 0 and j < 17:
                j += 1
        self.position_base = [0] * 51
        j = 0
        for i in range(51):
            self.position_base[i] = j
            j += 1 << self.extra_bits[i]

        self.R0 = self.R1 = self.R2 = 1
        self.header_read = False
        self.block_remaining = 0
        self.block_type = 0
        self.intel_filesize = 0
        self.intel_curpos = 0
        self.intel_started = False

        # huffman length arrays
        self.PRETREE_len = bytearray(PRETREE_MAXSYMBOLS + 64)
        self.MAINTREE_len = bytearray(MAINTREE_MAXSYMBOLS + 64)
        self.LENGTH_len = bytearray(LENGTH_MAXSYMBOLS + 64)
        self.ALIGNED_len = bytearray(ALIGNED_MAXSYMBOLS + 64)

        # huffman decode tables
        self.PRETREE_table = [0] * ((1 << PRETREE_TABLEBITS) + (PRETREE_MAXSYMBOLS * 2))
        self.MAINTREE_table = [0] * ((1 << MAINTREE_TABLEBITS) + (MAINTREE_MAXSYMBOLS * 2))
        self.LENGTH_table = [0] * ((1 << LENGTH_TABLEBITS) + (LENGTH_MAXSYMBOLS * 2))
        self.ALIGNED_table = [0] * ((1 << ALIGNED_TABLEBITS) + (ALIGNED_MAXSYMBOLS * 2))

        # bit buffer
        self.buf = b""
        self.inpos = 0
        self.bitbuf = 0
        self.bitsleft = 0

    # ── bit buffer ────────────────────────────────────────────────
    def init_bitstream(self, data: bytes, pos: int):
        self.buf = data
        self.inpos = pos
        self.bitbuf = 0
        self.bitsleft = 0

    def ensure_bits(self, bits: int):
        while self.bitsleft < bits:
            if self.inpos + 1 < len(self.buf):
                lo = self.buf[self.inpos]
                hi = self.buf[self.inpos + 1]
            elif self.inpos < len(self.buf):
                lo = self.buf[self.inpos]
                hi = 0
            else:
                lo = hi = 0
            self.inpos += 2
            self.bitbuf |= ((hi << 8) | lo) << (16 - self.bitsleft)
            self.bitbuf &= 0xFFFFFFFF
            self.bitsleft += 16

    def peek_bits(self, bits: int) -> int:
        return (self.bitbuf >> (32 - bits)) & 0xFFFFFFFF

    def remove_bits(self, bits: int):
        self.bitbuf = (self.bitbuf << bits) & 0xFFFFFFFF
        self.bitsleft -= bits

    def read_bits(self, bits: int) -> int:
        if bits == 0:
            return 0
        self.ensure_bits(bits)
        ret = self.peek_bits(bits)
        self.remove_bits(bits)
        return ret

    # ── huffman ───────────────────────────────────────────────────
    def make_decode_table(self, nsyms: int, nbits: int, length: bytearray, table: list) -> int:
        pos = 0
        table_mask = 1 << nbits
        bit_mask = table_mask >> 1
        bit_num = 1
        while bit_num <= nbits:
            for sym in range(nsyms):
                if length[sym] == bit_num:
                    leaf = pos
                    pos += bit_mask
                    if pos > table_mask:
                        return 1
                    fill = bit_mask
                    while fill > 0:
                        table[leaf] = sym
                        leaf += 1
                        fill -= 1
            bit_mask >>= 1
            bit_num += 1

        if pos != table_mask:
            for sym in range(pos, table_mask):
                table[sym] = 0xFFFF
            next_symbol = max(table_mask >> 1, nsyms)
            pos <<= 16
            table_mask <<= 16
            bit_mask = 1 << 15
            bit_num = nbits + 1
            while bit_num <= 16:
                for sym in range(nsyms):
                    if length[sym] == bit_num:
                        leaf = pos >> 16
                        for fill in range(bit_num - nbits):
                            if table[leaf] == 0xFFFF:
                                table[next_symbol << 1] = 0xFFFF
                                table[(next_symbol << 1) + 1] = 0xFFFF
                                table[leaf] = next_symbol
                                next_symbol += 1
                            leaf = table[leaf] << 1
                            if (pos >> (15 - fill)) & 1:
                                leaf += 1
                        table[leaf] = sym
                        pos += bit_mask
                        if pos > table_mask:
                            return 1
                bit_mask >>= 1
                bit_num += 1
            if pos != table_mask:
                return 1
        return 0

    def read_huffsym(self, table, length, nbits, maxsymbols) -> int:
        self.ensure_bits(16)
        i = table[self.peek_bits(nbits)]
        if i >= maxsymbols:
            j = 1 << (32 - nbits)
            while True:
                j >>= 1
                i <<= 1
                i |= 1 if (self.bitbuf & j) else 0
                if j == 0:
                    return 0
                i = table[i]
                if i < maxsymbols:
                    break
        self.remove_bits(length[i])
        return i

    def read_lengths(self, lens: bytearray, first: int, last: int):
        # read 20 pretree lengths (4 bits each)
        for i in range(PRETREE_NUM_ELEMENTS):
            self.PRETREE_len[i] = self.read_bits(4)
        self.make_decode_table(PRETREE_MAXSYMBOLS, PRETREE_TABLEBITS,
                               self.PRETREE_len, self.PRETREE_table)
        if DEBUG:
            print(f"  [read_lengths {first}..{last}] pretree_len={list(self.PRETREE_len[:20])}")
        i = first
        while i < last:
            z = self.read_huffsym(self.PRETREE_table, self.PRETREE_len,
                                  PRETREE_TABLEBITS, PRETREE_MAXSYMBOLS)
            if z == 17:
                y = self.read_bits(4) + 4
                while y > 0:
                    lens[i] = 0
                    i += 1
                    y -= 1
            elif z == 18:
                y = self.read_bits(5) + 20
                while y > 0:
                    lens[i] = 0
                    i += 1
                    y -= 1
            elif z == 19:
                y = self.read_bits(1) + 4
                z = self.read_huffsym(self.PRETREE_table, self.PRETREE_len,
                                      PRETREE_TABLEBITS, PRETREE_MAXSYMBOLS)
                z = lens[i] - z
                if z < 0:
                    z += 17
                while y > 0:
                    lens[i] = z
                    i += 1
                    y -= 1
            else:
                z = lens[i] - z
                if z < 0:
                    z += 17
                lens[i] = z
                i += 1

    def decompress(self, data: bytes, inpos: int, in_len: int, outbuf: bytearray, outpos: int, out_len: int):
        self.init_bitstream(data, inpos)
        window = self.window
        window_size = self.window_size
        window_posn = self.window_posn
        R0, R1, R2 = self.R0, self.R1, self.R2

        if not self.header_read:
            intel = self.read_bits(1)
            if intel:
                i = self.read_bits(16)
                j = self.read_bits(16)
                self.intel_filesize = (i << 16) | j
            self.header_read = True

        togo = out_len
        while togo > 0:
            if self.block_remaining == 0:
                self.block_type = self.read_bits(3)
                # LZX block length is 24 bits: (16 high) << 8 | (8 low)
                i = self.read_bits(16)
                j = self.read_bits(8)
                self.block_remaining = (i << 8) | j
                if DEBUG:
                    print(f"  [block] type={self.block_type} remaining={self.block_remaining}")

                if self.block_type == BLOCKTYPE_ALIGNED:
                    for i in range(8):
                        self.ALIGNED_len[i] = self.read_bits(3)
                    self.make_decode_table(ALIGNED_MAXSYMBOLS, ALIGNED_TABLEBITS,
                                           self.ALIGNED_len, self.ALIGNED_table)
                    self.read_lengths(self.MAINTREE_len, 0, 256)
                    self.read_lengths(self.MAINTREE_len, 256, self.main_elements)
                    self.make_decode_table(MAINTREE_MAXSYMBOLS, MAINTREE_TABLEBITS,
                                           self.MAINTREE_len, self.MAINTREE_table)
                    self.read_lengths(self.LENGTH_len, 0, NUM_SECONDARY_LENGTHS)
                    self.make_decode_table(LENGTH_MAXSYMBOLS, LENGTH_TABLEBITS,
                                           self.LENGTH_len, self.LENGTH_table)
                elif self.block_type == BLOCKTYPE_VERBATIM:
                    self.read_lengths(self.MAINTREE_len, 0, 256)
                    self.read_lengths(self.MAINTREE_len, 256, self.main_elements)
                    self.make_decode_table(MAINTREE_MAXSYMBOLS, MAINTREE_TABLEBITS,
                                           self.MAINTREE_len, self.MAINTREE_table)
                    self.read_lengths(self.LENGTH_len, 0, NUM_SECONDARY_LENGTHS)
                    self.make_decode_table(LENGTH_MAXSYMBOLS, LENGTH_TABLEBITS,
                                           self.LENGTH_len, self.LENGTH_table)
                elif self.block_type == BLOCKTYPE_UNCOMPRESSED:
                    # realign to 16-bit boundary, read R0,R1,R2 (12 bytes LE)
                    if self.bitsleft > 16:
                        self.inpos -= 2
                    # ensure even alignment: discard partial bits
                    self.bitsleft = 0
                    self.bitbuf = 0
                    R0 = struct.unpack_from("<I", data, self.inpos)[0]
                    R1 = struct.unpack_from("<I", data, self.inpos + 4)[0]
                    R2 = struct.unpack_from("<I", data, self.inpos + 8)[0]
                    self.inpos += 12
                else:
                    raise ValueError(f"bad block type {self.block_type}")

            this_run = self.block_remaining
            this_run = min(this_run, togo)
            togo -= this_run
            self.block_remaining -= this_run

            if self.block_type == BLOCKTYPE_VERBATIM:
                while this_run > 0:
                    main_element = self.read_huffsym(self.MAINTREE_table, self.MAINTREE_len,
                                                     MAINTREE_TABLEBITS, MAINTREE_MAXSYMBOLS)
                    if main_element < NUM_CHARS:
                        window[window_posn] = main_element
                        window_posn += 1
                        this_run -= 1
                    else:
                        main_element -= NUM_CHARS
                        match_length = main_element & NUM_PRIMARY_LENGTHS
                        if match_length == NUM_PRIMARY_LENGTHS:
                            length_footer = self.read_huffsym(self.LENGTH_table, self.LENGTH_len,
                                                              LENGTH_TABLEBITS, LENGTH_MAXSYMBOLS)
                            match_length += length_footer
                        match_length += MIN_MATCH

                        match_offset = main_element >> 3
                        if match_offset > 2:
                            if match_offset != 3:
                                extra = self.extra_bits[match_offset]
                                verbatim_bits = self.read_bits(extra)
                                match_offset = self.position_base[match_offset] - 2 + verbatim_bits
                            else:
                                match_offset = 1
                            R2, R1, R0 = R1, R0, match_offset
                        elif match_offset == 0:
                            match_offset = R0
                        elif match_offset == 1:
                            match_offset = R1
                            R1 = R0
                            R0 = match_offset
                        else:
                            match_offset = R2
                            R2 = R0
                            R0 = match_offset

                        rundest = window_posn
                        runsrc = rundest - match_offset
                        if runsrc < 0:
                            runsrc += window_size
                        this_run -= match_length
                        while match_length > 0:
                            window[rundest] = window[runsrc]
                            rundest += 1
                            runsrc += 1
                            if runsrc == window_size:
                                runsrc = 0
                            if rundest == window_size:
                                rundest = 0
                            match_length -= 1
                        window_posn = rundest
            elif self.block_type == BLOCKTYPE_ALIGNED:
                while this_run > 0:
                    main_element = self.read_huffsym(self.MAINTREE_table, self.MAINTREE_len,
                                                     MAINTREE_TABLEBITS, MAINTREE_MAXSYMBOLS)
                    if main_element < NUM_CHARS:
                        window[window_posn] = main_element
                        window_posn += 1
                        this_run -= 1
                    else:
                        main_element -= NUM_CHARS
                        match_length = main_element & NUM_PRIMARY_LENGTHS
                        if match_length == NUM_PRIMARY_LENGTHS:
                            length_footer = self.read_huffsym(self.LENGTH_table, self.LENGTH_len,
                                                              LENGTH_TABLEBITS, LENGTH_MAXSYMBOLS)
                            match_length += length_footer
                        match_length += MIN_MATCH

                        match_offset = main_element >> 3
                        if match_offset > 2:
                            extra = self.extra_bits[match_offset]
                            match_offset = self.position_base[match_offset] - 2
                            if extra > 3:
                                extra -= 3
                                verbatim_bits = self.read_bits(extra)
                                match_offset += (verbatim_bits << 3)
                                aligned_bits = self.read_huffsym(self.ALIGNED_table, self.ALIGNED_len,
                                                                 ALIGNED_TABLEBITS, ALIGNED_MAXSYMBOLS)
                                match_offset += aligned_bits
                            elif extra == 3:
                                aligned_bits = self.read_huffsym(self.ALIGNED_table, self.ALIGNED_len,
                                                                 ALIGNED_TABLEBITS, ALIGNED_MAXSYMBOLS)
                                match_offset += aligned_bits
                            elif extra > 0:
                                verbatim_bits = self.read_bits(extra)
                                match_offset += verbatim_bits
                            else:
                                match_offset = 1
                            R2, R1, R0 = R1, R0, match_offset
                        elif match_offset == 0:
                            match_offset = R0
                        elif match_offset == 1:
                            match_offset = R1
                            R1 = R0
                            R0 = match_offset
                        else:
                            match_offset = R2
                            R2 = R0
                            R0 = match_offset

                        rundest = window_posn
                        runsrc = rundest - match_offset
                        if runsrc < 0:
                            runsrc += window_size
                        this_run -= match_length
                        while match_length > 0:
                            window[rundest] = window[runsrc]
                            rundest += 1
                            runsrc += 1
                            if runsrc == window_size:
                                runsrc = 0
                            if rundest == window_size:
                                rundest = 0
                            match_length -= 1
                        window_posn = rundest
            elif self.block_type == BLOCKTYPE_UNCOMPRESSED:
                for _ in range(this_run):
                    window[window_posn] = data[self.inpos]
                    self.inpos += 1
                    window_posn += 1

        # copy this frame's output out of the window
        # window_posn now points just past the last byte written this call
        start = window_posn - out_len
        if start < 0:
            start += window_size
            outbuf[outpos:outpos + (window_size - start)] = window[start:window_size]
            wrote = window_size - start
            outbuf[outpos + wrote:outpos + out_len] = window[0:out_len - wrote]
        else:
            outbuf[outpos:outpos + out_len] = window[start:start + out_len]

        self.window_posn = window_posn % window_size
        self.R0, self.R1, self.R2 = R0, R1, R2


def xnb_decompress(path: str) -> tuple[bytes, str, int]:
    raw = open(path, "rb").read()
    assert raw[0:3] == b"XNB", "not an XNB"
    platform = chr(raw[3])
    version = raw[4]
    flags = raw[5]
    compressed_lzx = bool(flags & 0x80)
    compressed_lz4 = bool(flags & 0x40)
    # bytes 6..10 = total XNB file size (unused; we walk by explicit offsets)
    if not (compressed_lzx or compressed_lz4):
        # uncompressed: payload begins right after 10-byte header
        return raw[10:], platform, version
    if compressed_lz4:
        raise NotImplementedError("LZ4 path not needed for these assets")

    decompressed_size = struct.unpack_from("<I", raw, 10)[0]
    out = bytearray(decompressed_size)
    dec = LzxDecoder(16)
    pos = 14
    outpos = 0
    while outpos < decompressed_size:
        hi = raw[pos]
        lo = raw[pos + 1]
        pos += 2
        block_size = (hi << 8) | lo
        frame_size = 0x8000
        if hi == 0xFF:
            hi = lo
            lo = raw[pos]
            pos += 1
            frame_size = (hi << 8) | lo
            hi = raw[pos]
            lo = raw[pos + 1]
            pos += 2
            block_size = (hi << 8) | lo
        if block_size == 0 or frame_size == 0:
            break
        if outpos + frame_size > decompressed_size:
            frame_size = decompressed_size - outpos
        dec.decompress(raw, pos, block_size, out, outpos, frame_size)
        pos += block_size
        outpos += frame_size
    return bytes(out), platform, version


if __name__ == "__main__":
    path = sys.argv[1]
    data, platform, version = xnb_decompress(path)
    print(f"platform={platform} version={version}")
    print(f"decompressed bytes = {len(data)}")
    print(f"first 48 bytes hex = {data[:48].hex()}")
