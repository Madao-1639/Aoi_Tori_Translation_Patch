"""在原生 mog3x32.dll 上追加一个引导节，使其加载时执行我们的代码。

做法：不动任何已有节的内容，只在文件末尾追加新节 `.cnpt`，然后改 PE 头的两处：
  - AddressOfEntryPoint → 新节入口（DLL 的入口点是 DllMain，被加载时调用）
  - 节表加一项、SizeOfImage 加上新节占用的虚拟空间

为什么这样安全：原生 6 个节、导出表、重定位全部原地保留，游戏对 19 个导出
（MOG_Init 等）的调用仍走原代码，音频行为不变。

用法：
    python script/append_section.py --input <原生 mog3x32.dll> \
        --payload build/payload.bin --out patch/mog3x32.dll

payload.bin 是一个「位置无关、无重定位、不引用导入表」的机器码块，
入口签名与 DllMain 相同（stdcall, 3 参数, 返回 BOOL）。
"""
from __future__ import annotations

import argparse
import struct
import sys

PE32_MAGIC = 0x10B
SCN_CNT_CODE = 0x00000020
SCN_MEM_EXECUTE = 0x20000000
SCN_MEM_READ = 0x40000000
SCN_MEM_WRITE = 0x80000000
SCN_CNT_INITIALIZED_DATA = 0x00000040


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def append_section(image: bytes, payload: bytes, name: bytes = b".cnpt",
                  entry_rva: int = 0) -> bytes:
    if len(name) > 8:
        raise ValueError("节名最多 8 字节")
    if image[:2] != b"MZ":
        raise ValueError("不是 PE 文件")
    pe = struct.unpack_from("<I", image, 0x3C)[0]
    if image[pe:pe + 4] != b"PE\0\0":
        raise ValueError("PE 签名不符")

    machine, nsections, _, _, _, opt_size, _ = struct.unpack_from("<HHIIIHH", image, pe + 4)
    opt = pe + 24
    magic = struct.unpack_from("<H", image, opt)[0]
    if magic != PE32_MAGIC:
        raise ValueError("只支持 PE32，当前 magic=0x%x" % magic)

    size_of_image = struct.unpack_from("<I", image, opt + 56)[0]
    size_of_headers = struct.unpack_from("<I", image, opt + 60)[0]
    section_align, file_align = struct.unpack_from("<II", image, opt + 32)

    table = opt + opt_size
    if table + nsections * 40 + 40 > size_of_headers:
        raise ValueError("节表空间不足，无法再加一节")

    # 取最后一个节，新节接在它后面
    last = table + (nsections - 1) * 40
    last_vaddr = struct.unpack_from("<I", image, last + 12)[0]
    last_vsize = struct.unpack_from("<I", image, last + 8)[0]
    last_rawsize = struct.unpack_from("<I", image, last + 16)[0]
    last_rawoff = struct.unpack_from("<I", image, last + 20)[0]

    new_vaddr = align_up(last_vaddr + max(last_vsize, last_rawsize), section_align)
    new_rawoff = align_up(len(image), file_align)
    new_rawsize = align_up(len(payload), file_align)

    out = bytearray(image)
    out.extend(b"\0" * (new_rawoff - len(out)))
    out.extend(payload)
    out.extend(b"\0" * (new_rawsize - len(payload)))

    flags = SCN_CNT_CODE | SCN_MEM_EXECUTE | SCN_MEM_READ
    entry = struct.pack("<8sIIIIIIHHI",
                        name.ljust(8, b"\0"),
                        len(payload),           # VirtualSize
                        new_vaddr,              # VirtualAddress
                        new_rawsize,            # SizeOfRawData
                        new_rawoff,             # PointerToRawData
                        0, 0,                   # 重定位/行号
                        0, 0,                   # 无重定位、无行号
                        flags)

    # 写节表项（如果原来紧跟 40 字节空槽，直接覆盖）
    struct.pack_into("<8sIIIIIIHHI", out, table + nsections * 40, *struct.unpack("<8sIIIIIIHHI", entry))
    struct.pack_into("<H", out, pe + 6, nsections + 1)                       # NumberOfSections
    struct.pack_into("<I", out, opt + 16, new_vaddr + entry_rva)              # AddressOfEntryPoint
    struct.pack_into("<I", out, opt + 56, max(size_of_image,
                                              align_up(new_vaddr + len(payload), section_align)))
    # SizeOfCode 也加上（不是必需，但保持一致）
    size_of_code = struct.unpack_from("<I", out, opt + 4)[0]
    struct.pack_into("<I", out, opt + 4, size_of_code + new_rawsize)
    return bytes(out)


def probe(image: bytes):
    """返回新节将占用的 (虚拟地址, 文件偏移)。"""
    pe = struct.unpack_from("<I", image, 0x3C)[0]
    nsections = struct.unpack_from("<H", image, pe + 6)[0]
    opt_size = struct.unpack_from("<H", image, pe + 20)[0]
    opt = pe + 24
    section_align, file_align = struct.unpack_from("<II", image, opt + 32)
    table = opt + opt_size
    last = table + (nsections - 1) * 40
    last_vsize, last_vaddr, last_rawsize, _ = struct.unpack_from("<IIII", image, last + 8)
    new_vaddr = align_up(last_vaddr + max(last_vsize, last_rawsize), section_align)
    new_rawoff = align_up(len(image), file_align)
    return new_vaddr, new_rawoff


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="原生 mog3x32.dll")
    parser.add_argument("--payload", help="引导代码机器码")
    parser.add_argument("--out", help="输出文件")
    parser.add_argument("--name", default=".cnpt")
    parser.add_argument("--probe", action="store_true", help="只打印新节地址，不写文件")
    parser.add_argument("--entry-rva", type=lambda v: int(v, 0), default=0,
                        help="入口在新节内的偏移（链接器给出的入口 VA - 节 VA）")
    args = parser.parse_args()

    with open(args.input, "rb") as fh:
        image = fh.read()
    if args.probe:
        vaddr, rawoff = probe(image)
        print("probe %d %d" % (vaddr, rawoff))
        return 0
    if not args.payload or not args.out:
        print("需要 --payload 与 --out", file=sys.stderr)
        return 2

    with open(args.input, "rb") as fh:
        image = fh.read()
    with open(args.payload, "rb") as fh:
        payload = fh.read()
    if not payload:
        print("payload 为空", file=sys.stderr)
        return 1

    result = append_section(image, payload, args.name.encode("ascii"), args.entry_rva)
    with open(args.out, "wb") as fh:
        fh.write(result)
    print("%s + %d 字节 payload -> %s (%d -> %d 字节)"
          % (args.input, len(payload), args.out, len(image), len(result)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
