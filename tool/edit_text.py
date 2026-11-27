"""PS2A 文本就地编辑：只改 text 段内的字节，不动 code / unknown / 头部。

设计约束（就是 100% 验证的来源）：

    PS2A 载荷 = labels | code | unknown | text。字符串引用是 **text 段内的字节偏移**，
    偏移写在 code 段里，而 code 段的引用形态不止一种（实测见过
    `01 02 20 01 <u32> 0f 02`、`0f 02 20 01 <u32> 70 01 0f 02`、
    `01 02 20 01 <u32> 30 04` 等），靠模式匹配无法穷尽。

    所以本工具**不移动任何字节**：新串写入原偏移，剩余空间补 0，
    下一个串的起点保持不变。于是
      - code 段、unknown 段：逐字节不变
      - text_size / decoded_size / 整个 48 字节头部：不变
      - 所有引用的偏移：仍然有效
    唯一的变化是 text 段里被编辑串的那几个字节。

    代价：新串的 GBK 字节数必须 <= 原串可用空间（到下一个串起点之前）。
    日文原文比中文译文宽，所以「把漏译的日文换成中文」天然满足；
    反之（中文换更长的中文）会被拒绝并报错，不会静默写出可疑文件。

`verify()` 做四项断言，任何一项不成立即抛 EditError：
    1. code 段与 unknown 段逐字节不变；
    2. 头部 11 个 u32 逐字段不变；
    3. 每个被编辑串，从其偏移到原区间终点之间 == 新内容 + NUL 填充；
    4. 未被编辑的串，其区间与原来逐字节相同。

注意：就地写入后，被编辑串留下的 NUL 填充会让 `strings()` 切出额外的空串。
这是**预期行为**——引擎按偏移取串，读到第一个 NUL 就停；多余的空串没有任何
引用指向它们，无害。所以验证以**原串表**为准（按原偏移/原区间切分），
而不是比较新的 `strings()` 列表长度。
"""
from __future__ import annotations

import ps3

HEADER_FIELDS = 11


class EditError(Exception):
    pass


class TextEdit:
    """把 text 段里位于 offset 的那条串替换为 text。

    用**字节偏移**作键，不用串下标：就地写入会在被编辑串尾部留下 NUL 填充，
    使 `strings()` 多切出若干空串、后续下标全部偏移。偏移不受影响（正是
    就地编辑的意义），所以偏移是唯一稳定锚点。
    """

    def __init__(self, offset: int, text: str):
        self.offset = offset
        self.text = text

    def __repr__(self):
        return "TextEdit(@%d, %r)" % (self.offset, self.text)


def encode(text: str) -> bytes:
    try:
        return text.encode("gbk")
    except UnicodeEncodeError as exc:
        raise EditError("无法用 GBK 编码：%r（%s）" % (text, exc))


def regions(strings, text_len):
    """每条串占据的 (起点, 终点)：终点是下一个串的起点（最后一条到段尾）。"""
    out = []
    for i, (offset, _) in enumerate(strings):
        end = strings[i + 1][0] if i + 1 < len(strings) else text_len
        out.append((offset, end))
    return out


def available(strings, index, text_len):
    """该串可用字节数（不含终止 NUL）。"""
    start, end = regions(strings, text_len)[index]
    return end - start - 1


def plan(script, edits):
    """校验并返回 (旧串表, 新 payload 表)。

    新串必须放得下；同一偏移不许改两次。放不下直接报错，不做静默截断。
    """
    strings = script.strings()
    text_len = len(script.text)
    starts = {offset: i for i, (offset, _) in enumerate(strings)}
    payloads = [raw for _, raw in strings]
    seen = set()
    for edit in edits:
        if edit.offset in seen:
            raise EditError("同一偏移被改两次：@%d" % edit.offset)
        seen.add(edit.offset)
        if edit.offset not in starts:
            raise EditError("偏移 @%d 不是任何串的起点" % edit.offset)
        index = starts[edit.offset]
        raw = encode(edit.text)
        room = available(strings, index, text_len)
        if len(raw) > room:
            raise EditError("@%d 放不下：新串 %d 字节，可用 %d 字节。"
                            "就地编辑不改偏移，长出来的一条必须换短写法。"
                            % (edit.offset, len(raw), room))
        payloads[index] = raw
    return strings, payloads


def apply_edits(script, edits, *, check=True):
    """就地应用 edits，返回新 Script。"""
    strings, payloads = plan(script, edits)
    text = bytearray(script.text)
    for index, raw in enumerate(payloads):
        if raw == strings[index][1]:
            continue
        start, end = regions(strings, len(text))[index]
        text[start:end] = raw + b"\0" * (end - start - len(raw))

    header = list(script.header)                 # 一个字段都不改
    code_start = script.code_start
    code_end = code_start + script.code_size
    unknown_end = code_end + script.unknown_size
    out = ps3.Script(header,
                     script.payload[:unknown_end] + bytes(text),
                     script.trailing)
    if check:
        verify(out, script, edits, strings)
    return out


def verify(new, old, edits, old_strings):
    """四项断言，任一失败即抛错。以**原串表**的偏移与区间为准。"""
    if new.header != old.header:
        raise EditError("头部被改动：%r -> %r" % (old.header, new.header))
    if new.code != old.code:
        raise EditError("code 段被改动")
    if new.unknown != old.unknown:
        raise EditError("unknown 段被改动")
    if len(new.text) != len(old.text):
        raise EditError("text 段长度变化：%d -> %d" % (len(old.text), len(new.text)))

    by_offset = {e.offset: e for e in edits}
    old_regions = regions(old_strings, len(old.text))
    new_text = new.text
    for index, (offset, old_raw) in enumerate(old_strings):
        a, b = old_regions[index]
        if offset in by_offset:
            want = encode(by_offset[offset].text)
            if len(want) > b - a - 1:
                raise EditError("@%d 写越界" % offset)
            expect = want + b"\0" * (b - a - len(want))
            if new_text[a:b] != expect:
                raise EditError("@%d 字节区间不符：%r != %r"
                                % (offset, new_text[a:b], expect))
            # 引擎按偏移读，遇到第一个 NUL 停止 → 必须读到新内容
            got = new.string_at(offset)
            if got != want:
                raise EditError("@%d 引擎读出不符：%r != %r" % (offset, got, want))
        else:
            if new_text[a:b] != old.text[a:b]:
                raise EditError("@%d 未编辑却被改动" % offset)
            if new.string_at(offset) != old_raw:
                raise EditError("@%d 未编辑串读出被改动" % offset)
