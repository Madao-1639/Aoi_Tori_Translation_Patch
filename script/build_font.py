"""构建随包字体：把思源黑体 CN（OFL）按本补丁实际用字子集化，出 Regular + Bold 两个 face。

字体族名沿用引擎原本请求的名字（`MS Gothic`），引擎侧配置、游戏内字体选择列表、
`cmvs.cfg` 的 `FONT=` 全都不用改；字形与度量由我们控制（这就是随包字体的目的）。

为什么要两个 face：引擎只请求两档字重（exe `.rdata` 0x4D1AC0 的 `cWeight` =
{0, 200, 400, 600, 600}，索引 2=正常、4=粗体，即 400 / 600）。只放一个 face 时
GDI 只能就近取（正常文本偏粗）或自行合成加粗（笔画发毛）。放进字体自带的
Medium(500) 与 Bold(700)，GDI 会把 400→Medium、600→Bold 自动选好，引擎侧零改动。

用法：
  python script/build_font.py \
      --medium <SourceHanSansCN-Medium.otf> --bold <SourceHanSansCN-Bold.otf>
产物：patch/cnpatch/font/SourceHanSansCN-Medium.ttf、-Bold.ttf
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tool"))

import ps3  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAMILY = "思源黑体"   # 中文名：引擎的字体名框处于双字节模式，只认 GBK 文本
FAMILY_CN = FAMILY

# 引擎会用到的 ASCII 与全角标点，无论译文是否出现都保留
EXTRA = (
    "".join(chr(c) for c in range(0x20, 0x7F))
    + "　、。，．・：；？！ー～…‘’“”（）〔〕［］｛｝〈〉《》「」『』【】±×÷→←↑↓℃％‰"
    + "♪♫★☆○●◎◇◆△▲▽▼※№"
    # 字体自身的显示名也要有字形，否则设置界面那个字体名输入框会是豆腐块
    + "思源黑体宋楷圆雅黑"
)


def used_characters(script_dir):
    """收集所有中文脚本用到的字。

    **不允许静默跳过**：某个串解不出 GBK 说明脚本里混进了非 GBK 字节（历史日文残留、
    写错的编码等），这时字母表会默默缺字、实机表现为豆腐块。所以解不出就记账，
    收集完一并报错，让问题在构建阶段暴露。

    唯一允许的例外：内容里没有非 ASCII 字节的串（纯 ASCII 控制串/文件名）
    本来就不需要 GBK 解码，直接跳过。
    """
    chars = set(EXTRA)
    count = 0
    undecodable = []
    for name in sorted(os.listdir(script_dir)):
        if not name.lower().endswith(".ps3"):
            continue
        count += 1
        script = ps3.load(os.path.join(script_dir, name))
        for offset, raw in script.strings():
            if not raw or all(0x20 <= byte < 0x7F for byte in raw):
                continue
            try:
                text = raw.decode("gbk")
            except UnicodeDecodeError as exc:
                undecodable.append((name, offset, raw[:40], str(exc)))
                continue
            chars.update(text)
    return chars, count, undecodable


def to_truetype(font, max_err=1.0):
    """CFF 轮廓转 glyf（TrueType）。

    必须在注册进 GDI 前完成：CFF(OTF) 字体在 EnumFontFamiliesExA 里报 fonttype=2(DEVICE)，
    而游戏列字体只收 fonttype=4(TRUETYPE)，所以 OTF 永远不会出现在游戏内字体列表里。
    """
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.ttLib import newTable

    order = font.getGlyphOrder()
    glyph_set = font.getGlyphSet()
    glyf = newTable("glyf")
    glyf.glyphOrder = order
    glyf.glyphs = {}
    for name in order:
        pen = TTGlyphPen(glyph_set)
        glyph_set[name].draw(Cu2QuPen(pen, max_err, reverse_direction=True))
        glyf.glyphs[name] = pen.glyph()

    font["glyf"] = glyf
    font.sfntVersion = chr(0) + chr(1) + chr(0) + chr(0)   # TrueType sfnt version (OTTO would keep CFF)
    font["loca"] = newTable("loca")
    from fontTools.ttLib.tables._m_a_x_p import table__m_a_x_p
    maxp = table__m_a_x_p()
    maxp.tableVersion = 0x00010000
    maxp.numGlyphs = len(order)
    maxp.maxPoints = 0
    maxp.maxContours = 0
    maxp.maxCompositePoints = 0
    maxp.maxCompositeContours = 0
    maxp.maxZones = 2
    maxp.maxTwilightPoints = 0
    maxp.maxStorage = 0
    maxp.maxFunctionDefs = 0
    maxp.maxInstructionDefs = 0
    maxp.maxStackElements = 0
    maxp.maxSizeOfInstructions = 0
    maxp.maxComponentElements = 0
    maxp.maxComponentDepth = 0
    font["maxp"] = maxp
    font["head"].indexToLocFormat = 0
    font["post"].formatType = 3.0   # 不需要字形名，避免 post 2.0 的 mapping 要求
    for tag in ("CFF ", "VORG"):
        if tag in font:
            del font[tag]
    return font


def build(source, out, subfamily, codepoints):
    from fontTools import subset
    from fontTools.ttLib import TTFont

    options = subset.Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.name_legacy = True
    options.notdef_outline = True
    options.recalc_bounds = True
    options.drop_tables += ["DSIG"]

    font = TTFont(source, fontNumber=0)
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=codepoints)
    subsetter.subset(font)

    bold = subfamily == "Bold"

    # 族名必须两个 face 完全一致，否则 GDI 会把它们当两个字族（列表里出现两行）。
    # 源字体的 name ID 1 带字重后缀（"Source Han Sans CN Medium"），这里统一规范化。
    name = font["name"]
    for record in list(name.names):
        if record.nameID in (1, 2, 3, 4, 6, 16, 17, 18, 20, 21, 22):
            name.removeNames(nameID=record.nameID)
    full = FAMILY if not bold else FAMILY + " Bold"
    postscript = FAMILY.replace(" ", "")
    postscript = postscript if not bold else postscript + "-Bold"
    for name_id, value in ((1, FAMILY), (16, FAMILY), (20, FAMILY),
                           (2, subfamily), (17, subfamily), (21, subfamily),
                           (3, full), (4, full), (18, full), (22, full), (6, postscript)):
        for language in (0x409, 0x804):             # 两种语言写同一串，GDI 取哪条都一致
            name.setName(value, name_id, 3, 1, language)

    os2 = font["OS/2"]
    os2.fsType = 0                                      # OFL：允许嵌入/安装
    os2.ulCodePageRange1 |= 0x00040000                  # 简体中文 GB2312(936)
    os2.ulCodePageRange1 |= 0x00020000                  # 繁体中文 Big5(950)
    os2.ulCodePageRange1 |= 0x00010000                  # 日文 Shift-JIS(932)
    if bold:
        os2.fsSelection = (os2.fsSelection | 0x0020) & ~0x0040   # BOLD
        font["head"].macStyle |= 0x01
    else:
        os2.fsSelection = (os2.fsSelection | 0x0040) & ~0x0020   # REGULAR
        font["head"].macStyle &= ~0x01

    to_truetype(font)
    font.save(out)
    print("written %s (%.1f KiB, weight %d, %s)"
          % (out, os.path.getsize(out) / 1024.0, os2.usWeightClass, subfamily))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--medium", required=True)
    parser.add_argument("--bold", required=True)
    parser.add_argument("--scripts", default=os.path.join(ROOT, "resource", "patch", "scripts"))
    parser.add_argument("--out-dir", default=os.path.join(ROOT, "patch", "cnpatch"))
    args = parser.parse_args()

    chars, scripts, undecodable = used_characters(args.scripts)
    codepoints = sorted(ord(c) for c in chars if c.isprintable() or c in "　♪")
    print("scripts %d, characters %d" % (scripts, len(codepoints)))
    if undecodable:
        # 字表会因这些串而缺点字，实机表现为豆腐块，不得静默通过
        print("ERROR 有 %d 个串无法按 GBK 解码，字表不完整：" % len(undecodable))
        for name, offset, head, why in undecodable[:20]:
            print("   %-11s @%-6d %r（%s）" % (name, offset, head, why))
        if len(undecodable) > 20:
            print("   ……共 %d 个" % len(undecodable))
        return 1
    os.makedirs(args.out_dir, exist_ok=True)
    os.makedirs(os.path.join(args.out_dir, "font"), exist_ok=True)

    build(args.medium, os.path.join(args.out_dir, "font", "SourceHanSansCN-Medium.ttf"), "Regular", codepoints)
    build(args.bold, os.path.join(args.out_dir, "font", "SourceHanSansCN-Bold.ttf"), "Bold", codepoints)
    return 0


if __name__ == "__main__":
    sys.exit(main())
