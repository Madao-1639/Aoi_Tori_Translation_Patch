"""CPZ7 归档成员名读取（只解析索引，不解资源）。

用于「汉化包的图对应游戏里哪个文件名」这类反查：索引里有完整成员名，
不需要解出资源内容。

算法来源：cmvs-rs `cmvs-core/src/cpz/cpz7.rs` + `crypto.rs`（Darkness-TX 的参考实现）。
注意 CPZ7 与 CPZ6 的头部/索引加密不同，不能混用。

用法：
    python tool/cpz7.py <某个.cpz> [--grep 关键词]
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys

HEADER_SIZE = 72
TABLE_BYTES = bytes([
    0x89, 0xf0, 0x90, 0xcd, 0x82, 0xb7, 0x82, 0xe9, 0x88, 0xab, 0x82, 0xa2, 0x8e, 0x71, 0x82, 0xcd,
    0x83, 0x8a, 0x83, 0x52, 0x82, 0xaa, 0x82, 0xa8, 0x8e, 0x64, 0x92, 0x75, 0x82, 0xab, 0x82, 0xb5,
    0x82, 0xbf, 0x82, 0xe1, 0x82, 0xa2, 0x82, 0xdc, 0x82, 0xb7, 0x81, 0x42, 0x8e, 0xf4, 0x82, 0xed,
    0x82, 0xea, 0x82, 0xbf, 0x82, 0xe1, 0x82, 0xa2, 0x82, 0xdc, 0x82, 0xb7, 0x82, 0xe6, 0x81, 0x60,
    0x81, 0x41, 0x82, 0xc6, 0x82, 0xa2, 0x82, 0xa4, 0x82, 0xa9, 0x82, 0xe0, 0x82, 0xa4, 0x8e, 0xf4,
    0x82, 0xc1, 0x82, 0xbf, 0x82, 0xe1, 0x82, 0xa2, 0x82, 0xdc, 0x82, 0xb5, 0x82, 0xbd, 0x81, 0xf4,
])

MASK = (1 << 32) - 1
MD5_K = [
    0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
    0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
    0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa, 0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
    0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a,
    0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
    0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
    0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
    0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1, 0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391,
]


def word(b: bytes, off: int = 0) -> int:
    return struct.unpack_from("<I", b, off)[0]


def rotl(v: int, n: int) -> int:
    v &= MASK
    return ((v << n) | (v >> (32 - n))) & MASK


def rotr(v: int, n: int) -> int:
    v &= MASK
    return ((v >> n) | (v << (32 - n))) & MASK


def md5_state_compress(state, block):
    a, b, c, d = state
    initial = state[:]
    for i in range(64):
        if i < 16:
            f, g, s = (d ^ (b & (c ^ d))), i, [7, 12, 17, 22][i % 4]
        elif i < 32:
            f, g, s = (c ^ (d & (b ^ c))), (5 * i + 1) % 16, [5, 9, 14, 20][i % 4]
        elif i < 48:
            f, g, s = (b ^ c ^ d), (3 * i + 5) % 16, [4, 11, 16, 23][i % 4]
        else:
            f, g, s = (c ^ (b | ((~d) & MASK))), (7 * i) % 16, [6, 10, 15, 21][i % 4]
        nxt = (a + f + MD5_K[i] + block[g]) & MASK
        nxt = (rotl(nxt, s) + b) & MASK
        a, d, c, b = d, c, b, nxt
    return [(initial[0] + a) & MASK, (initial[1] + b) & MASK,
            (initial[2] + c) & MASK, (initial[3] + d) & MASK]


def custom_digest(input_words, initial):
    block = [0] * 16
    block[:4] = input_words
    block[4] = 0x80
    block[14] = 128
    return md5_state_compress(initial, block)


def keys_from_header_v7(header: bytes):
    masks = [0x43de7c1a, 0xcc65f416, 0xd016a93d, 0x97a3ba9b]
    inp = [(word(header, 32 + 4 * i) ^ masks[i]) & MASK for i in range(4)]
    state = custom_digest(inp, [0xc74a2b02, 0xe7c8ab8f, 0x38bebc4e, 0x7531a4c3])
    digest = [
        (state[2] ^ 0x53a76d2e) & MASK,
        (state[1] + 0x5bb17fda) & MASK,
        (state[0] + 0x6853e14d) & MASK,
        (state[3] ^ 0xf5c6a9a3) & MASK,
    ]
    key = (word(header, 48) ^ 0xae7d39b7) & MASK
    seed = rotr((word(header, 56) ^ 0x37acf832) & MASK, 5)
    seed = (seed * 0x7da8f173 + 0x13712765) & MASK
    return key, seed, digest


def directory_key(key, digest):
    return [
        (digest[0] ^ ((key + 0x76a3bf29) & MASK)) & MASK,
        (key ^ digest[1]) & MASK,
        (digest[2] ^ ((key + 0x10000000) & MASK)) & MASK,
        (key ^ digest[3]) & MASK,
    ]


def file_key(directory, digest):
    return [
        (directory ^ digest[0]) & MASK,
        (((directory + 0x11003322) & MASK) ^ digest[1]) & MASK,
        (directory ^ digest[2]) & MASK,
        (((directory + 0x34216785) & MASK) ^ digest[3]) & MASK,
    ]


def make_table(key: int, seed: int):
    table = list(range(256))
    k = key
    for _ in range(256):
        table[(k >> 16) & 0xFF], table[k & 0xFF] = table[k & 0xFF], table[(k >> 16) & 0xFF]
        table[(k >> 8) & 0xFF], table[(k >> 24) & 0xFF] = table[(k >> 24) & 0xFF], table[(k >> 8) & 0xFF]
        k = (rotr(k, 2) * 0x1a74f195 + seed) & MASK
    return table


def substitute(buf: bytearray, key: int, seed: int, xor: int):
    table = make_table(key, seed)
    for i, b in enumerate(buf):
        buf[i] = table[(b ^ xor) & 0xFF]


def outer_index_v7(buf: bytearray, key: int):
    k = key ^ 0x3795b39a
    table = [(word(TABLE_BYTES, 4 * i) - k) & MASK for i in range(24)]
    shift = (((k ^ (k >> 8) ^ (k >> 16) ^ (k >> 24) ^ 0xFFFFFFFB) & 15) + 7) & 31
    flag = 5
    n_full = len(buf) // 4 * 4
    for off in range(0, n_full, 4):
        v = word(buf, off)
        v = ((v ^ table[flag]) + 0x784c5062) & MASK
        v = rotl(v, (32 - shift) % 32)
        v = (v + 0x01010101) & MASK
        struct.pack_into("<I", buf, off, v)
        flag = (flag + 1) % 24
    # 尾部：countdown_tail 时移位 = 剩余字节数（RS 直接用它做 >> 4*n，
    # x86 的 shift 会按 32 取模，这里同样取模）
    length = len(buf)
    for i in range(n_full, length):
        shift4 = ((4 * (length - i)) & 31)
        buf[i] = ((buf[i] ^ ((table[flag] >> shift4) & 0xFF)) - 0x7D) & 0xFF
        flag = (flag + 1) % 24


def directory_index(buf: bytearray, key: int, digest, step: int):
    substitute(buf, key, digest[1], 0x3A)
    dk = directory_key(key, digest)
    seed = 0x76548AEF
    flag = 0
    n_full = len(buf) // 4 * 4
    for off in range(0, n_full, 4):
        v = ((word(buf, off) ^ dk[flag]) - 0x4A91C262) & MASK
        v = rotl(v, 3)
        v = (v - seed) & MASK
        struct.pack_into("<I", buf, off, v)
        seed = (seed + step) & MASK
        flag = (flag + 1) & 3
    for i in range(n_full, len(buf)):
        buf[i] = ((buf[i] ^ ((dk[flag] >> 6) & 0xFF)) + 0x37) & 0xFF
        flag = (flag + 1) & 3


def file_index(buf: bytearray, key: int, digest, directory: int, step: int):
    substitute(buf, key, digest[2], 0x7E)
    fk = file_key(directory, digest)
    seed = 0x2A65CB4F
    flag = 0
    n_full = len(buf) // 4 * 4
    for off in range(0, n_full, 4):
        v = ((word(buf, off) ^ fk[flag]) - seed) & MASK
        v = rotl(v, 2)
        v = (v + 0x37A19E8B) & MASK
        struct.pack_into("<I", buf, off, v)
        seed = (seed - step) & MASK
        flag = (flag + 1) & 3
    for i in range(n_full, len(buf)):
        buf[i] = ((buf[i] ^ ((fk[flag] >> 4) & 0xFF)) + 3) & 0xFF
        flag = (flag + 1) & 3


def parse_layout(header: bytes, file_bytes: int):
    """CPZ7 头部字段全部是异或掩码后的值（见 cmvs-rs Cpz7Layout::parse）。"""
    field = lambda at: word(header, at)
    initial = (field(64) - 0x6DC5A9B4) & MASK
    checksum = (data_checksum(header[:64]) - 0x5A902B7C + initial) & MASK
    if checksum != field(68):
        raise ValueError("CPZ7 header checksum mismatch")
    index_key_bytes = field(64) ^ 0x65EF99F3
    if index_key_bytes < 24:
        raise ValueError("CPZ7 index-key block too short")
    directory_count = field(4) ^ 0xFE3A53DA
    directory_index_bytes = field(8) ^ 0x37F298E8
    file_index_bytes = field(12) ^ 0x7A6F3A2D
    encryption_mode = field(52) ^ 0xFB73A956
    data_offset = (HEADER_SIZE + directory_index_bytes + file_index_bytes + index_key_bytes)
    return (directory_count, directory_index_bytes, file_index_bytes,
            encryption_mode, index_key_bytes, data_offset, file_bytes - data_offset)


def data_checksum(buf: bytes) -> int:
    """0x5a902b7c 起，按 LE u32 逐字累加，尾部字节逐个累加。"""
    total = 0x5A902B7C
    n = len(buf) // 4 * 4
    for off in range(0, n, 4):
        total = (total + word(buf, off)) & MASK
    for i in range(n, len(buf)):
        total = (total + buf[i]) & MASK
    return total


class _Bits:
    """按位读取，每位的来源字节先与该字节下标%4 对应的 xor 值异或。"""

    def __init__(self, data: bytes, xor: bytes):
        self.data = data
        self.bit = 0
        self.xor = xor

    def get(self, count: int) -> int:
        value = 0
        for _ in range(count):
            at = self.bit // 8
            if at >= len(self.data):
                raise ValueError("truncated CPZ7 Huffman stream")
            bit = ((self.data[at] ^ self.xor[at & 3]) >> (self.bit & 7)) & 1
            value = (value << 1) | bit
            self.bit += 1
        return value


def _huffman_tree(bits: _Bits, nodes: list) -> int:
    if bits.get(1) == 0:
        return bits.get(8)
    if len(nodes) >= 255:
        raise ValueError("CPZ7 Huffman tree exceeds 255 internal nodes")
    at = len(nodes)
    nodes.append([0, 0])
    left = _huffman_tree(bits, nodes)
    right = _huffman_tree(bits, nodes)
    nodes[at] = [left, right]
    return at + 256


def unpack_index_key(block: bytes) -> bytes:
    """key 块：md5(rest)[16] + rest；rest 里 16..20 是解压后长度，20..24 是 xor，24.. 是 Huffman 流。"""
    if len(block) < 24:
        raise ValueError("truncated CPZ7 key block")
    if hashlib.md5(block[16:]).digest() != block[:16]:
        raise ValueError("CPZ7 index-key digest mismatch")
    length = word(block, 16)
    if not (0x3FF <= length <= 8 << 20):
        raise ValueError("CPZ7 unpacked key length outside bounds: %d" % length)
    bits = _Bits(block[24:], block[20:24])
    nodes: list = []
    root = _huffman_tree(bits, nodes)
    out = bytearray()
    for _ in range(length):
        symbol = root
        while symbol >= 256:
            left, right = nodes[symbol - 256]
            symbol = left if bits.get(1) == 0 else right
        out.append(symbol)
    return bytes(out)


def list_members(path: str):
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        file_bytes = fh.tell()
        fh.seek(0)
        header = fh.read(HEADER_SIZE)
        if header[:4] != b"CPZ7":
            raise ValueError("not CPZ7: %r" % header[:4])
        (directory_count, dir_bytes, file_bytes_index, mode,
         index_key_bytes, data_offset, _) = parse_layout(header, file_bytes)
        if mode > 1:
            raise ValueError("unsupported encryption mode %d" % mode)
        index_len = data_offset - HEADER_SIZE
        fh.seek(HEADER_SIZE)
        blob = bytearray(fh.read(index_len))

    index_total = dir_bytes + file_bytes_index
    # 整个索引区（目录索引 + 文件索引 + key 块）的 md5 与 header[16..32] 比对
    if hashlib.md5(bytes(blob)).digest() != header[16:32]:
        raise ValueError("CPZ7 encrypted index digest mismatch")
    key_block = bytes(blob[index_total:])
    index_key = unpack_index_key(key_block)
    # 先截断到索引区，再异或（与 cmvs-rs 顺序一致）
    blob = blob[:index_total]
    for i in range(len(blob)):
        blob[i] ^= index_key[(i + 3) % 0x3FF]

    key, seed, digest = keys_from_header_v7(header)
    outer_index_v7(blob, key)
    # 注意：bytearray 切片是副本，必须写回
    dirs = bytearray(blob[:dir_bytes])
    files = bytearray(blob[dir_bytes:])
    directory_index(dirs, key, digest, 0x10FB562A)

    out = []
    offset = 0
    directories = []
    for _ in range(directory_count):
        rec_len = word(dirs, offset)
        if rec_len < 17:
            raise ValueError("short directory record")
        rec = bytes(dirs[offset:offset + rec_len])
        name = rec[16:].split(b"\0")[0]
        count = word(rec, 4)
        foff = word(rec, 8)
        dkey = word(rec, 12)
        directories.append((name, count, foff, dkey))
        offset += rec_len
    if offset != len(dirs):
        raise ValueError("directory count/length mismatch")

    for i, (dname, count, foff, dkey) in enumerate(directories):
        end = directories[i + 1][2] if i + 1 < len(directories) else len(files)
        block = bytearray(files[foff:end])
        file_index(block, key, digest, dkey, 0x0139FA9B)
        off = 0
        for _ in range(count):
            rec_len = word(block, off)
            if rec_len < 29:
                raise ValueError("short file record")
            rec = bytes(block[off:off + rec_len])
            rel = word(rec, 4) | (word(rec, 8) << 32)
            size = word(rec, 12)
            fname = rec[28:].split(b"\0")[0].decode("shift_jis", "replace")
            out.append((fname, rel, size, dname.decode("shift_jis", "replace")))
            off += rec_len
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("archive")
    ap.add_argument("--grep", default=None)
    args = ap.parse_args()
    members = list_members(args.archive)
    hits = [m for m in members if args.grep is None or args.grep in m[0]]
    print("%s: %d 个成员，匹配 %d" % (args.archive, len(members), len(hits)))
    for fname, rel, size, dname in hits:
        print("  %-28s %9d  %s" % (fname, size, dname))
    return 0


if __name__ == "__main__":
    sys.exit(main())
