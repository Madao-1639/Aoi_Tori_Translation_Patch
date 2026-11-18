"""把汉化组的界面图（原始 BGRA）编码成引擎可读的 PB3B type 1，放进外挂目录。

汉化组 `Longinus.dat` 里的图片是**裸 BGRA**（无容器），文件名可用脚本里出现的
`*.pb3` 字符串反查（`script/extract_patch.py` 已完成）。尺寸/位深从游戏本身的
同名 PB3B（`data/image/update` 或 `data/image/chip` 的松散文件）读取，保证一致。

产物：resource/patch/pictures_pb3/<name>（再由 script/build_patch.py 拷进 patch/）

`resource/ui/<name>.png` 是**人工修改过的版本**（补字、重绘等），优先级高于
Longinus.dat 里的原图——存在时就用它，不存在才回退到提取源。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tool"))

import cpz7  # noqa: E402
import pb3  # noqa: E402
from PIL import Image  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGINAL_DIRS = ("data/image/update", "data/image/chip", "data/image/event")


def find_original(game, name):
    """先找磁盘上的松散文件；找不到则到 *.cpz 封包里取（如 bg*.pb3 只在 bg.cpz 里）。"""
    for folder in ORIGINAL_DIRS:
        path = os.path.join(game, *folder.split("/"), name)
        if os.path.isfile(path):
            return path
    return find_in_archive(game, name)


def find_in_archive(game, name):
    """从封包取成员资源；返回临时文件路径或 None。

    CPZ7 的资源解密不在 tool/cpz7.py 的范围内（那支只解索引），
    所以这里只用于「读取原图尺寸」，做法是先取索引确认存在，
    再借游戏已解出的同名 PB3B（data/image/update）——若两者都没有则放弃。
    """
    pack = os.path.join(game, "data", "pack")
    if not os.path.isdir(pack):
        return None
    for entry in sorted(os.listdir(pack)):
        if not entry.lower().endswith(".cpz"):
            continue
        try:
            members = cpz7.list_members(os.path.join(pack, entry))
        except Exception:
            continue
        for member, offset, size, _directory in members:
            if member != name:
                continue
            print("note %-24s 位于 %s（%d 字节）；资源解密未实现，需外部解出" % (name, entry, size))
            return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", required=True)
    parser.add_argument("--pictures", default=os.path.join(ROOT, "resource", "patch", "pictures"))
    parser.add_argument("--out", default=os.path.join(ROOT, "resource", "patch", "pictures_pb3"))
    parser.add_argument("--ui", default=os.path.join(ROOT, "resource", "ui"),
                        help="人工修改过的图（.png），优先于提取源")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    written = skipped = failed = 0
    for name in sorted(os.listdir(args.pictures)):
        if not name.lower().endswith(".pb3"):
            continue
        raw = None
        ui_png = os.path.join(args.ui, name[:-4] + ".png") if name.lower().endswith(".pb3") else None
        if ui_png and os.path.isfile(ui_png):
            raw = Image.open(ui_png).convert("RGBA").tobytes()
            print("ui   %-24s 使用 %s" % (name, os.path.relpath(ui_png, ROOT)))
        else:
            raw = open(os.path.join(args.pictures, name), "rb").read()
        original = find_original(args.game, name)
        if not original:
            print("skip %-24s 找不到同名原图" % name)
            skipped += 1
            continue
        width, height, bpp, _ = pb3.decode(open(original, "rb").read())
        # 汉化裸图恒为 4 字节/像素；按原图位深编码（24 位原图只取 RGB）
        if len(raw) != width * height * 4:
            print("skip %-24s 原图 %dx%d/%dbpp，裸图 %d 字节（期望 %d）"
                  % (name, width, height, bpp, len(raw), width * height * 4))
            skipped += 1
            continue
        encoded = pb3.encode(width, height, bpp, raw)
        # 回读校验：解出来必须与原图逐字节相同
        w2, h2, bpp2, decoded = pb3.decode(encoded)
        if (w2, h2, bpp2) != (width, height, bpp) or decoded != raw:
            print("FAIL %-24s 回读不一致" % name)
            failed += 1
            continue
        with open(os.path.join(args.out, name), "wb") as stream:
            stream.write(encoded)
        written += 1
    total = sum(os.path.getsize(os.path.join(args.out, n)) for n in os.listdir(args.out))
    print("images: written %d, skipped %d, failed %d, total %.1f MiB"
          % (written, skipped, failed, total / 1048576.0))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
