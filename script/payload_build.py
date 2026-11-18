"""从链接产物里抽出 .text 与 .rdata，按真实 RVA 排布成追加载荷。"""
import struct
import sys

raw_path, out_path = sys.argv[1], sys.argv[2]
raw = open(raw_path, "rb").read()
pe = struct.unpack_from("<I", raw, 0x3C)[0]
nsec = struct.unpack_from("<H", raw, pe + 6)[0]
optsize = struct.unpack_from("<H", raw, pe + 20)[0]
table = pe + 24 + optsize

SEC_FLAG_CODE = 0x20

entries = []
for i in range(nsec):
    off = table + i * 40
    name = raw[off:off + 8].rstrip(b"\0").decode("latin1")
    vsize, vaddr, rsize, roff = struct.unpack_from("<IIII", raw, off + 8)
    flags = struct.unpack_from("<I", raw, off + 36)[0]
    if name not in (".text", ".rdata"):
        continue
    entries.append((vaddr, raw[roff:roff + rsize], name))

entries.sort()
base_vaddr = entries[0][0]
blob = bytearray()
detail = []
for vaddr, data, name in entries:
    rel = vaddr - base_vaddr
    if rel < len(blob):
        raise SystemExit("overlap at %s" % name)
    blob.extend(b"\0" * (rel - len(blob)))
    blob.extend(data)
    detail.append("%s@+0x%x" % (name, rel))

open(out_path, "wb").write(bytes(blob))
print("payload %d bytes (%s)" % (len(blob), " ".join(detail)))
