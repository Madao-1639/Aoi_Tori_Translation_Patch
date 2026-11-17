"""构建随包字体：从思源黑体 CN（OFL）子集化出本补丁实际用到的字符。

字体族名沿用引擎原本请求的名字（`MS Gothic`），这样引擎侧配置、游戏内字体选择列表、
`cmvs.cfg` 的 `FONT=` 都不用改；字形与度量由我们控制（这就是随包字体的目的）。

用法：python script/build_font.py --source <SourceHanSansCN-Medium.otf> [--bold <...Bold.otf>]
产物：patch/cnpatch/font.otf
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tool"))

import ps3  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAMILY = "MS Gothic"

# 引擎会用到的 ASCII 与全角标点，无论译文是否出现都保留
EXTRA = (
    "".join(chr(c) for c in range(0x20, 0x7F))
    + "　、。，．・：；？！ー～…‘’“”（）〔〕［］｛｝〈〉《》「」『』【】±×÷→←↑↓℃％‰"
    + "♪♫★☆○●◎◇◆△▲▽▼※№"
)


def used_characters(script_dir):
    chars = set(EXTRA)
    count = 0
    for name in sorted(os.listdir(script_dir)):
        if not name.lower().endswith(".ps3"):
            continue
        count += 1
        script = ps3.load(os.path.join(script_dir, name))
        for _, raw in script.strings():
            try:
                text = raw.decode("gbk")
            except UnicodeDecodeError:
                continue
            chars.update(text)
    return chars, count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--scripts", default=os.path.join(ROOT, "resource", "patch", "scripts"))
    parser.add_argument("--out", default=os.path.join(ROOT, "patch", "cnpatch", "font.otf"))
    args = parser.parse_args()

    from fontTools import subset
    from fontTools.ttLib import TTFont

    chars, scripts = used_characters(args.scripts)
    codepoints = sorted(ord(c) for c in chars if c.isprintable() or c in "　♪")
    print("scripts %d, characters %d" % (scripts, len(codepoints)))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    options = subset.Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.name_legacy = True
    options.notdef_outline = True
    options.recalc_bounds = True
    options.drop_tables += ["DSIG"]

    font = TTFont(args.source, fontNumber=0)
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=codepoints)
    subsetter.subset(font)

    # 字体族名沿用引擎请求的名字
    name = font["name"]
    for record in list(name.names):
        if record.nameID in (1, 3, 4, 6, 16, 18, 20, 21, 22):
            name.removeNames(nameID=record.nameID)
    for name_id, value in ((1, FAMILY), (3, FAMILY), (4, FAMILY), (6, FAMILY.replace(" ", "")),
                           (16, FAMILY), (18, FAMILY), (20, FAMILY), (21, "Regular"),
                           (22, "Regular")):
        name.setName(value, name_id, 3, 1, 0x409)
        name.setName(value, name_id, 3, 1, 0x804)   # 简体中文
    name.setName("Regular", 2, 3, 1, 0x409)
    name.setName("Regular", 2, 3, 1, 0x804)
    name.setName("Version 1.000; AoiTori patch subset", 5, 3, 1, 0x409)

    os2 = font["OS/2"]
    os2.fsType = 0                       # OFL：允许嵌入/安装
    os2.ulCodePageRange1 |= 0x00040000   # 简体中文 GB2312(936)
    os2.ulCodePageRange1 |= 0x00020000   # 繁体中文 Big5(950)
    os2.ulCodePageRange1 |= 0x00010000   # 日文 Shift-JIS(932)
    os2.sxHeight = os2.sxHeight if os2.sxHeight else int(os2.sCapHeight * 0.75)
    os2.fsSelection = (os2.fsSelection & ~0x0021) | 0x0040   # regular

    font.save(args.out)
    size = os.path.getsize(args.out)
    print("written %s (%.1f KiB)" % (args.out, size / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
