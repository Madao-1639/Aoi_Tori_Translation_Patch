"""补译汉化组遗漏的条目（原文未译的整句、说话人名、章节标题）。

数据源是两件人工成果，都在 resource/ 里，**不写进本脚本**：

    resource/patch/retranslate.json    待补译清单
        键 "文件名:字节偏移"，值 {"ja": 原文, "zh": 译文}
        用**偏移**而不是串下标作键：就地写入会在被编辑串尾部留 NUL 填充，
        使 strings() 多切出空串、后续下标整体漂移；偏移则恒定不变。

    resource/patch/scripts/*.ps3        现有中文脚本（就地编辑的输入）

输出写回 resource/patch/scripts/，再由 script/build_patch.py 组装到 patch/。
**绝不直接改游戏目录**。

为什么能保证 100% 正确（三段论）：

    1. 原脚本的 text 段是致密的 `串\\0串\\0...`，每条串紧贴下一条（86 + 84 份
       文件里 349965 条串的「可用空间-实际长度」分布只有 {0: N} 一个值）。
    2. PS2A 头部**没有字符串数量字段**，172475 条串全部被 code 段的 u32 偏移
       引用（零例外）⇒ 引擎只按偏移取串，从不遍历数串。
    3. 因此：只要偏移不变、串内容在第一个 NUL 前正确，引擎读到的就一定是新内容。
       就地写入 + NUL 填充满足这一点，且 code / unknown / 头部一字不动。

幂等：文件里该偏移的内容要么是清单里的原文、要么已经是译文，两种都接受；
出现第三种内容说明文件被别的流程改过，报错而不是覆盖。

放不下（译文比原文长）时**报错退出**，不截断、不重排、不静默跳过。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tool"))

import edit_text  # noqa: E402
import ps3  # noqa: E402

DEFAULT_MANIFEST = os.path.join(ROOT, "resource", "patch", "retranslate.json")
DEFAULT_SCRIPTS = os.path.join(ROOT, "resource", "patch", "scripts")


def load_manifest(path):
    """返回 ({文件名: [(偏移, 原文, 译文, 上一次译文), ...]}, [未填译文的键])。"""
    with open(path, encoding="utf-8") as stream:
        data = json.load(stream)
    entries = data.get("entries", data)
    grouped, todo = {}, []
    for key, value in sorted(entries.items()):
        name, _, offset = key.partition(":")
        if not name or not offset.isdigit():
            raise SystemExit("清单键格式应为 '文件名:字节偏移'：%r" % key)
        zh = (value.get("zh") or "").strip()
        if not zh:
            todo.append(key)
            continue
        previous = (value.get("zh_from") or "").strip()
        grouped.setdefault(name, []).append((int(offset), value.get("ja", ""), zh, previous))
    return grouped, todo


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--scripts", default=DEFAULT_SCRIPTS)
    parser.add_argument("--dry-run", action="store_true", help="只报告，不写文件")
    args = parser.parse_args()

    if not os.path.isfile(args.manifest):
        raise SystemExit("找不到清单：%s" % args.manifest)
    grouped, todo = load_manifest(args.manifest)
    if todo:
        print("清单里还有 %d 条未填译文，本次跳过：" % len(todo))
        for key in todo[:20]:
            print("   %s" % key)
        if len(todo) > 20:
            print("   ……共 %d 条" % len(todo))

    total = changed = 0
    for name in sorted(grouped):
        path = os.path.join(args.scripts, name)
        if not os.path.isfile(path):
            raise SystemExit("清单引用了不存在的脚本：%s" % name)
        script = ps3.load(path)
        edits = []
        for offset, ja, zh, previous in grouped[name]:
            current = script.string_at(offset)
            if current is None:
                raise SystemExit("%s @%d 不是任何串的起点" % (name, offset))
            zh_bytes = edit_text.encode(zh)
            # 合法的当前值只有三种：日文原文（首次补译）、新译文（已经写过）、
            # 上一次的译文（修订已有译文，必须在清单里用 zh_from 声明）。
            # 除此之外一律报错，不静默覆盖。
            accepted = [zh_bytes]
            if ja:
                accepted.append(edit_text.encode(ja))
            if previous:
                accepted.append(edit_text.encode(previous))
            if current not in accepted:
                raise SystemExit(
                    "%s @%d 内容对不上：清单原文 %r / 译文 %r%s，文件里是 %r"
                    % (name, offset, ja, zh,
                       (" / 上一次译文 %r" % previous) if previous else "",
                       current))
            edits.append(edit_text.TextEdit(offset, zh))
        # 空编辑也要跑一遍：等于逐字节复现，是「零改动等价」的持续校验
        out = edit_text.apply_edits(script, edits)
        raw = out.encode()
        back = ps3.Script.decode(raw)
        if back.payload != out.payload:
            raise SystemExit("%s 往返不一致" % name)
        total += len(edits)
        if edits and not args.dry_run:
            with open(path, "wb") as stream:
                stream.write(raw)
            changed += 1
        for offset, _, zh, _ in grouped[name]:
            print("   %-11s @%-6d -> %s" % (name, offset, zh[:40].replace("\n", " ")))

    print("补译 %d 条，写入 %d 份脚本%s"
          % (total, changed, "（dry-run 未写盘）" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
