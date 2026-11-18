#!/usr/bin/env bash
# 在原生 mog3x32.dll 上追加引导节，产出补丁用的 DLL。
#
#   M2W64_BIN=<工具链 bin 目录> bash dll/build_loader.sh <原生 mog3x32.dll>
#
# 产物：patch/mog3x32.dll（原生文件 + 追加的 .cnpt 节）
#
# 引导节只做一件事：转调原生初始化后开线程，等 gdi32 就绪，
# 再 LoadLibrary("cnpatch\cnpatch.dll")。中文支持全在 cnpatch.dll 里。
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/.." && pwd)"
native="${1:-}"
bin="${M2W64_BIN:-}"

if [ -z "$native" ] || [ ! -f "$native" ]; then
  echo "usage: bash dll/build_loader.sh <native mog3x32.dll>" >&2
  exit 2
fi
if [ -z "$bin" ]; then
  for c in "$(command -v i686-w64-mingw32-gcc || true)" \
           "$(command -v x86_64-w64-mingw32-gcc || true)"; do
    [ -n "$c" ] && bin="$(dirname "$c")" && break
  done
fi
[ -n "$bin" ] || { echo "toolchain not found: set M2W64_BIN" >&2; exit 1; }

gcc="$bin/x86_64-w64-mingw32-gcc"; [ -x "$gcc" ] || gcc="$bin/i686-w64-mingw32-gcc"
ld="$bin/ld.exe"; [ -x "$ld" ] || ld="$bin/ld"
objcopy="$bin/objcopy.exe"; [ -x "$objcopy" ] || objcopy="$bin/objcopy"

work="$root/tmp/build"
mkdir -p "$work" "$root/patch"

# 1) 新节的 RVA
new_va=$(python "$root/script/append_section.py" --input "$native" --probe | awk '{print $2}')
echo "== new section RVA = 0x$(printf %x "$new_va")"

# 2) 原生入口 RVA（我们的入口做完事后转调它，否则跳过 CRT/TLS 会 0xc0000142）
orig_entry=$(python - "$native" <<'PY'
import struct, sys
raw = open(sys.argv[1], "rb").read()
pe = struct.unpack_from("<I", raw, 0x3C)[0]
print("0x%x" % struct.unpack_from("<I", raw, pe + 24 + 16)[0])
PY
)
echo "== native entry RVA $orig_entry"

# 3) 编译
flags=(-m32 -O2 -ffreestanding -nostdlib -fno-stack-protector -fno-pic
       -fno-asynchronous-unwind-tables -fno-common -Wall -Wextra
       "-DORIG_ENTRY_RVA=$orig_entry")
"$gcc" "${flags[@]}" -c "$here/bootstrap.c" -o "$work/bootstrap.o"

# 4) 链接。ld 恒定把 .text 放在 ImageBase+0x1000、.rdata 放在 +0x2000，
#    所以取 ImageBase = 10000000 + new_va - 1000，使 .text 恰好落在 new_va。
image_base=$((0x10000000 + new_va - 0x1000))
"$ld" -m i386pe --image-base "$image_base" --section-alignment 0x1000 \
      -e "_DllMain@12" -o "$work/bootstrap.raw" "$work/bootstrap.o"

# 5) 拼 payload。各节按 ld 分配的 RVA 排布并补 0：
#    .text -> +0x0000（=new_va），.rdata -> +0x1000。
#    ★ 必须按真实 RVA 排布：代码里的绝对地址按 RVA 计算，
#      漏掉空隙会让字符串落到节外，加载即段错误。
"$objcopy" -O binary -j .text  "$work/bootstrap.raw" "$work/sec_text.bin"
"$objcopy" -O binary -j .rdata "$work/bootstrap.raw" "$work/sec_rdata.bin"
python "$root/script/payload_build.py" "$work/bootstrap.raw" "$work/payload.bin"

# 6) 入口：ld 的 AddressOfEntryPoint 是 RVA，减去 .text 的 RVA 得节内偏移
read -r entry_rva text_rva <<EOF
$(python - "$work/bootstrap.raw" <<'PY'
import struct, sys
raw = open(sys.argv[1], "rb").read()
pe = struct.unpack_from("<I", raw, 0x3C)[0]
en = struct.unpack_from("<I", raw, pe + 24 + 16)[0]
nsec = struct.unpack_from("<H", raw, pe + 6)[0]
optsize = struct.unpack_from("<H", raw, pe + 20)[0]
table = pe + 24 + optsize
trva = struct.unpack_from("<I", raw, table + 12)[0]
print(en, trva)
PY
)
EOF
entry_off=$((entry_rva - text_rva))
echo "== entry offset in section = +0x$(printf %x "$entry_off")"

# 7) 追回原生 DLL
python "$root/script/append_section.py" --input "$native" \
    --payload "$work/payload.bin" --entry-rva "$entry_off" \
    --out "$root/patch/mog3x32.dll"

ls -l "$root/patch/mog3x32.dll"
