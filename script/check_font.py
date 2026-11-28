"""离线校验随包字体（不启动游戏，可 CI 复跑）。

三项断言，任何一项失败即返回 1：

1. **能进游戏字体列表**：`EnumFontFamiliesExA` 回调里 `fonttype == 4`(TRUETYPE)、`lfCharSet == 134`。
   `fonttype=2`(DEVICE) 的 OTF/CFF 永远进不了游戏列表（`doc/engine-mechanics.md` §12）。
2. **用字零缺字**：中文脚本里出现的每个非控制字符都能在 cmap 里找到。
3. **垂直度量 == 字号**：`TEXTMETRIC.tmHeight` 等于请求的字号、`tmInternalLeading == 0`。
   思源黑体原版的 1.448 em 行框会让引擎算错行数，人名行被拆行压到正文上（§11.4）。
   同一字号下也把 MS Gothic 量一遍作对照 —— 只看「与系统字体是否一致」不足以判据，
   但差异出现时必须能解释。

用法：
  python script/check_font.py
"""
from __future__ import annotations

import ctypes
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tool"))

import ps3  # noqa: E402

FONT_DIR = os.path.join(ROOT, "patch", "cnpatch", "font")
SCRIPTS = os.path.join(ROOT, "resource", "patch", "scripts")
FACE = "思源黑体".encode("gbk")
FR_PRIVATE = 0x10
GB2312_CHARSET = 134
CONTROL = set(range(0x20)) | {0x7F}

gdi32 = ctypes.WinDLL("gdi32")
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateFontA.restype = ctypes.c_void_p
gdi32.CreateFontA.argtypes = [ctypes.c_int] * 5 + [ctypes.c_ubyte] * 8 + [ctypes.c_char_p]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.GetTextMetricsA.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.AddFontResourceExA.argtypes = [ctypes.c_char_p, ctypes.c_ulong, ctypes.c_void_p]
gdi32.EnumFontFamiliesExA.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]

ENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_ulong, ctypes.c_void_p)


def text_metrics(hdc):
    """TEXTMETRICA 关键字段（结构见 wingdi.h；用原始字节取值，避免 ctypes 对齐坑）。"""
    buf = ctypes.create_string_buffer(96)
    gdi32.GetTextMetricsA(hdc, buf)
    raw = buf.raw
    height, ascent, descent, internal, external = struct.unpack_from("<5i", raw, 0)
    average = struct.unpack_from("<i", raw, 20)[0]
    return {"height": height, "ascent": ascent, "descent": descent,
            "internal": internal, "external": external, "average": average}


def enum_face(hdc, face):
    """返回 [(lfCharSet, fonttype)]，只收指定族名的记录。"""
    found = []

    def callback(logfont, metrics, fonttype, param):
        raw = bytes(ctypes.cast(logfont, ctypes.POINTER(ctypes.c_ubyte * 60)).contents)
        name = raw[28:60].split(b"\0")[0]
        if name == face:
            found.append((raw[23], fonttype))
        return 1

    reference = ENUMPROC(callback)
    logfont = ctypes.create_string_buffer(60)
    struct.pack_into("<i", logfont, 0, -12)
    logfont[0x17] = GB2312_CHARSET
    logfont[28:28 + len(face)] = face
    gdi32.EnumFontFamiliesExA(hdc, logfont, reference, None, 0)
    return found


def used_characters():
    from fontTools.ttLib import TTFont          # 仅此处需要，延迟导入

    used = set()
    for name in sorted(os.listdir(SCRIPTS)):
        if not name.lower().endswith(".ps3"):
            continue
        for _, raw in ps3.load(os.path.join(SCRIPTS, name)).strings():
            if not raw or all(0x20 <= byte < 0x7F for byte in raw):
                continue
            try:
                used.update(raw.decode("gbk"))
            except UnicodeDecodeError:
                pass
    return used


def main() -> int:
    if not os.path.isdir(FONT_DIR):
        print("找不到字体目录：%s（先跑 script/build_font.py）" % FONT_DIR)
        return 1
    fonts = [os.path.join(FONT_DIR, name) for name in sorted(os.listdir(FONT_DIR))
             if name.lower().endswith(".ttf")]
    if not fonts:
        print("字体目录里没有 .ttf：%s" % FONT_DIR)
        return 1
    for path in fonts:
        added = gdi32.AddFontResourceExA(path.encode(), FR_PRIVATE, None)
        print("AddFontResourceEx %-34s -> %d" % (os.path.basename(path), added))

    from fontTools.ttLib import TTFont
    used = used_characters()
    hdc = gdi32.CreateCompatibleDC(None)
    failures = 0

    print("\n[1] 能进游戏字体列表（游戏只收 fonttype=4）")
    records = enum_face(hdc, FACE)
    if not records:
        print("    FAIL 枚举不到族名 %r" % FACE.decode("gbk"))
        failures += 1
    for charset, fonttype in records:
        ok = charset == GB2312_CHARSET and fonttype == 4
        print("    %s lfCharSet=%d fonttype=%d" % ("OK  " if ok else "FAIL", charset, fonttype))
        failures += 0 if ok else 1

    print("\n[2] 中文脚本用字零缺字（共 %d 字）" % len(used))
    for path in fonts:
        cmap = TTFont(path).getBestCmap()
        missing = sorted(ch for ch in used
                         if ord(ch) not in cmap and ord(ch) not in CONTROL)
        print("    %s %-34s 缺 %d %s"
              % ("OK  " if not missing else "FAIL", os.path.basename(path),
                 len(missing), "".join(missing[:20])))
        failures += 0 if not missing else 1

    print("\n[3] 垂直度量 == 字号（tmInternalLeading 必须为 0）")
    for height in (16, 20, 24, 28, 32, 40, 48):
        font = gdi32.CreateFontA(-height, 0, 0, 0, 400, 0, 0, 0, GB2312_CHARSET,
                                 0, 0, 0, 0x30, FACE)
        old = gdi32.SelectObject(hdc, font)
        metrics = text_metrics(hdc)
        ok = abs(metrics["height"] - height) <= 1 and metrics["internal"] == 0
        print("    %s 请求 %2dpx -> tmHeight %2d asc %2d desc %2d intLead %2d ave %2d"
              % ("OK  " if ok else "FAIL", height, metrics["height"], metrics["ascent"],
                 metrics["descent"], metrics["internal"], metrics["average"]))
        failures += 0 if ok else 1
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(font)

    # 对照：同一字号下的系统中文字体。差异本身不是判据，但必须能解释；
    # 我们的字体 tmHeight 应等于 MS Gothic 的 tmHeight（都等于字号）。
    reference = gdi32.CreateFontA(-28, 0, 0, 0, 400, 0, 0, 0, 128,
                                  0, 0, 0, 0x30, b"MS Gothic")
    old = gdi32.SelectObject(hdc, reference)
    metrics = text_metrics(hdc)
    print("\n    对照 MS Gothic 28px：tmHeight %d asc %d desc %d intLead %d ave %d"
          % (metrics["height"], metrics["ascent"], metrics["descent"],
             metrics["internal"], metrics["average"]))
    gdi32.SelectObject(hdc, old)
    gdi32.DeleteObject(reference)
    gdi32.DeleteDC(hdc)

    if failures:
        print("\n%d 项校验失败" % failures)
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
