#!/usr/bin/env bash
# 构建补丁 DLL（cnpatch.dll）。
#
# 需要 32 位目标的 gcc + ld。本机可用 m2w64（x86_64 目标但支持 -m32 出 pe-i386，
# 用 `ld -m i386pe` 链接）；也可用任意 i686-w64-mingw32 工具链。
#
#   M2W64_BIN=<工具链 bin 目录> bash dll/build.sh [--debug]
#
#   --debug   写入 cnpatch/cnpatch.log 诊断日志（发布版默认不写盘）
#
# 产物：patch/cnpatch/cnpatch.dll（补丁本体）
#       patch/mog3x32.dll 由 dll/build_loader.sh 生成（在原生 DLL 上追加引导节）
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/.." && pwd)"
bin="${M2W64_BIN:-}"
if [ -z "$bin" ]; then
  for candidate in "$(command -v i686-w64-mingw32-gcc || true)" \
                   "$(command -v x86_64-w64-mingw32-gcc || true)"; do
    [ -n "$candidate" ] && bin="$(dirname "$candidate")" && break
  done
fi
[ -n "$bin" ] || { echo "找不到工具链：请设置 M2W64_BIN 指到 mingw bin 目录" >&2; exit 1; }

gcc="$bin/x86_64-w64-mingw32-gcc"
[ -x "$gcc" ] || gcc="$bin/i686-w64-mingw32-gcc"
[ -x "$gcc" ] || { echo "工具链里没有 gcc：$bin" >&2; exit 1; }
ld="$bin/ld.exe"; [ -x "$ld" ] || ld="$bin/ld"

flags=(-m32 -O2 -ffreestanding -nostdlib -fno-stack-protector -fno-asynchronous-unwind-tables -fno-common -Wall -Wextra)

# 发布版不写日志（不改动用户磁盘）。需要诊断时加 --debug。
debug=0
for arg in "$@"; do
  case "$arg" in
    --debug) debug=1 ;;
    *) echo "未知参数：$arg（可用：--debug）" >&2; exit 2 ;;
  esac
done
if [ "$debug" = "1" ]; then
  flags+=(-DCNPATCH_DEBUG)
  echo "== 调试构建：会写 cnpatch/cnpatch.log"
fi

mkdir -p "$root/patch/cnpatch"
echo "== cnpatch"
"$gcc" "${flags[@]}" -c "$here/cnpatch.c" -o "$here/cnpatch.o"
# cnpatch 是运行期 LoadLibrary 进来的：必须给一个不容易被占用的首选基址。
# 本工具链（binutils 2.25 i386pe）产出的 DLL 不生成 .reloc，首选基址被占时
# LoadLibrary 会以 ERROR_INVALID_ADDRESS(487) 失败，所以两个 DLL 分开基址。
"$ld" -m i386pe --dll --image-base 0x6A000000 --entry _DllMain@12 \
      -o "$root/patch/cnpatch/cnpatch.dll" "$here/cnpatch.o"

ls -l "$root/patch/cnpatch/cnpatch.dll"
