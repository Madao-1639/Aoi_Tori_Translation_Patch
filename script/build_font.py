"""构建随包字体：把思源黑体 CN（OFL）按本补丁实际用字子集化，出 Medium + Bold 两个 face。

字体族名用 GBK「思源黑体」，引擎侧的默认字体串由补丁就地改成同名（`doc/engine-mechanics.md` §11.2）。

为什么要两个 face：引擎只请求两档字重（exe `.rdata` 0x4D1AC0 的 `cWeight` =
{0, 200, 400, 600, 600}，索引 2=正常、4=粗体，即 400 / 600）。只放一个 face 时
GDI 只能就近取（正常文本偏粗）或自行合成加粗（笔画发毛）。放进字体自带的
Medium(500) 与 Bold(700)，GDI 会把 400→Medium、600→Bold 自动选好，引擎侧零改动。

**垂直度量必须归一到 1em 行高**（见 `normalize_metrics`）：思源黑体原版的排版度量是
1.448em，直接沿用会让 GDI 报出的 tmHeight / tmAscent / tmInternalLeading 与系统
中文字体不一致，引擎侧表现为对话框人名被误换行。

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


def normalize_metrics(font):
    """把「行框高度」归一到字体自身的 em 框（1em），不改字形的实际尺寸。

    思源黑体原版的垂直度量是排版用的宽行框：hhea 1160/-288、OS/2 的
    usWinAscent/usWinDescent 也是 1160/288，合计 1.448em。子集化后这套值原样留下，
    于是引擎请求 28px 字体时，GDI 报出 tmHeight 40 / tmAscent 32 /
    tmInternalLeading 12 —— 而字体实际只有 28px 高。

    引擎把这个结构当成**一行的高度**（对话框按「可用行高 / tmHeight」算能放几行）：
    行框被报大 1.448 倍，算出能放的行数就少了，人名行被拆到下一行，
    压在下方的正文上。换字体之所以「缓解」，就是因为系统字体的 tmHeight 正好等于字号。

    做法：用字体**自己的** em 框（OS/2 sTypoAscender/sTypoDescender，在思源黑体里是
    880/-120，合计正好 1000 = upem）作为行框。实测该框能装下全部 CJK 字形
    （字集内全角字符最高 yMax 868、最低 yMin -108，见 `measure_ink_box`），所以这不是
    模仿某个系统字体，而是回到字体自己设计的 1em 行高。

    为什么动 usWin 而不是只改 hhea：GDI 的 TEXTMETRIC 只从 usWinAscent/usWinDescent
    取 tmAscent/tmDescent（实测：只改 hhea 完全无效，只改 usWin 立即生效）。
    sTypo* 与 hhea 一并改成同一组值，避免不同渲染路径（以及用 typo 度量的软件）
    取到不一样的行高。
    """
    upem = font["head"].unitsPerEm
    os2, hhea = font["OS/2"], font["hhea"]

    ascent = os2.sTypoAscender
    descent = -os2.sTypoDescender
    if ascent + descent != upem:
        raise SystemExit("字体自身的 em 框不是 1em（sTypo %d/%d，upem %d），不敢自行缩放"
                         % (ascent, -descent, upem))

    os2.usWinAscent = ascent
    os2.usWinDescent = descent
    os2.sTypoLineGap = 0            # 非 0 会被算进 tmInternalLeading，行高超过 1em
    hhea.ascent = ascent
    hhea.descent = -descent
    hhea.lineGap = 0

    # tmAveCharWidth 保持半角字宽（GBK 字符集里一个汉字 = 两字节 = 两个字宽）
    os2.xAvgCharWidth = upem // 2

    # 全角字形的 advance 不得宽于 1em：超出 em 框的字符会让按 em 宽累加的排版溢出。
    # 字集里只有 U+2116(№, 1030) 越界；半角的 proportional advance 保持原样，
    # 引擎对半角字母数字有单独的按词回溯逻辑（exe 0x385E8），不该拉成等宽。
    hmtx = font["hmtx"]
    cmap = font.getBestCmap()
    for codepoint, glyph in cmap.items():
        advance, side_bearing = hmtx[glyph]
        if advance > upem:
            hmtx[glyph] = (upem, side_bearing)

    # 未映射的字形：子集化会保留源字体里的绘图元件（源里是 2~3 em 宽的长条，
    # 例如 glyph03586 宽 2854/1000em）。它们不在 cmap 里、也进不了 GBK 字集，
    # 但会污染字体级度量（hhea.advanceWidthMax 3000、GDI 的 tmMaxCharWidth 83px）。
    # 引擎只用 tmHeight / tmAscent（exe 内 GetTextMetricsA 仅 0x614C5 一处调用），
    # 所以这不影响排版；但留着一组访问不到的怪度量没有任何好处，一并清掉。
    cmap_names = set(cmap.values())
    glyf = font["glyf"]
    dropped = 0
    for name in font.getGlyphOrder():
        if name in cmap_names or name not in glyf.glyphs:
            continue
        glyph = glyf[name]
        if glyph.numberOfContours and not glyph.isComposite():
            glyph.coordinates = None
            glyph.flags = None
            glyph.endPtsOfContours = []
            glyph.numberOfContours = 0
            dropped += 1
        hmtx[name] = (0, 0)          # 无字形也不给它占宽度

    hhea.advanceWidthMax = max(hmtx[glyph][0] for glyph in cmap_names)
    return ascent, descent, dropped


def measure_ink_box(font, codepoints):
    """字符集内「全角字符」的纵向范围，用于验证 em 框装得下（与字体度量无关）。

    只统计 CJK 字符（汉字/标点/假名）：西文（'g' 下伸 -247、'y' 下伸 -230、'|' 下伸 -281）
    天然超出 descent，系统字体也一样（MS Gothic / SimSun 的 'g' 都在 descent 之下），
    引擎按整格排版，所以它们不是异常。真正会造成「行与行重叠」的是汉字/标点/假名超出 em 框。
    """
    glyf = font["glyf"]
    cmap = font.getBestCmap()
    top, bottom = 0, 0
    for codepoint in codepoints:
        if (0x20 <= codepoint <= 0x7E            # ASCII
                or 0xA1 <= codepoint <= 0xAF      # Latin-1 符号
                or 0xFF01 <= codepoint <= 0xFF5E):  # 全角 ASCII 形式（字形与 ASCII 同形，同样带下伸）
            continue                                    # 西文，不进 em 框校验
        name = cmap.get(codepoint)
        if not name:
            continue
        glyph = glyf[name]
        if glyph.numberOfContours == 0:
            continue
        glyph.recalcBounds(glyf)
        top = max(top, glyph.yMax)
        bottom = min(bottom, glyph.yMin)
    return top, bottom


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
    ascent, descent, dropped = normalize_metrics(font)
    top, bottom = measure_ink_box(font, codepoints)
    if top > ascent or -bottom > descent:
        print("WARNING 全角字集 ink box %d/%d 超出 em 框 %d/-%d（字形会溢出到相邻行）"
              % (top, bottom, ascent, descent))
    font.save(out)
    print("written %s (%.1f KiB, weight %d, %s, 行框 %d/-%d = %.3fem, 全角 ink %d/%d, 清掉未映射字形 %d)"
          % (out, os.path.getsize(out) / 1024.0, os2.usWeightClass, subfamily,
             ascent, descent, (ascent + descent) / float(font["head"].unitsPerEm),
             top, bottom, dropped))


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
