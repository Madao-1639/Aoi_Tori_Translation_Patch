"""生成最小升级包：只打包相对上一版的差异文件。

依据是**磁盘上的实际内容**，不是 Git 记录：对比 patch/ 与游戏目录，找出
「补丁里有、但内容不同」的文件。这样升级包永远等于「把当前 patch/ 铺上去时
真正会被改动的那些文件」。

用法：
    python script/build_update.py                     # 按 release 里的上一版对比
    python script/build_update.py --baseline <目录>   # 与指定目录对比（如自解压的旧版）
    python script/build_update.py --dry-run           # 只报告

输出：
    releases/Aoi_Tori_CN_Patch_v<版本>-update.zip     仅含差异文件 + 升级说明

安全约束（与项目 §7 一致）：
    - 只读游戏目录，绝不写入；
    - 补丁里有、游戏目录没有的文件**不打包**（那是首次安装该给的，不是升级内容），
      但会在报告里单独列出，提醒是否需要处理；
    - 生成后校验：每个条目的 SHA-256 必须与 patch/ 里的一致。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tool"))

# 补丁会写入游戏的三个子树（与 README 的安装布局一致）
TREES = ("data/update", "data/image/update", "cnpatch")
# 需要单独比对的单文件
FILES = ("mog3x32.dll",)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def scan(root):
    """递归收集 root 下 TREES 与 FILES 的 {相对路径: sha256}。"""
    found = {}
    for tree in TREES:
        base = os.path.join(root, *tree.split("/"))
        if not os.path.isdir(base):
            continue
        for here, _, names in os.walk(base):
            for name in names:
                full = os.path.join(here, name)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                found[rel] = sha256_file(full)
    for name in FILES:
        full = os.path.join(root, name)
        if os.path.isfile(full):
            found[name] = sha256_file(full)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", default=os.path.join(ROOT, "patch"),
                        help="要发布的内容（默认 patch/）")
    parser.add_argument("--baseline", default=None,
                        help="对比基准目录；省略则用 --game 的游戏目录")
    parser.add_argument("--game", default=None, help="游戏目录（只读）")
    parser.add_argument("--version", default=None, help="升级包版本号，默认读 patch/manifest.json")
    parser.add_argument("--out-dir", default=os.path.join(ROOT, "releases"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    baseline = args.baseline or args.game
    if not baseline or not os.path.isdir(baseline):
        raise SystemExit("需要一个可读的基准目录（--baseline 或 --game）")

    built = scan(args.build)
    installed = scan(baseline)

    changed = sorted(k for k in built if k in installed and built[k] != installed[k])
    missing = sorted(k for k in built if k not in installed)   # 首次安装才需要
    removed = sorted(k for k in installed if k not in built)    # 新版不再提供

    print("基准：%s" % baseline)
    print("  内容变化：%d 个" % len(changed))
    for key in changed:
        print("     %s" % key)
    if missing:
        print("  游戏目录里没有（不计入升级包，需整包安装）：%d 个" % len(missing))
        for key in missing[:10]:
            print("     %s" % key)
    if removed:
        print("  新版不再提供（升级包不会删除）：%d 个" % len(removed))
        for key in removed[:10]:
            print("     %s" % key)

    if not changed:
        print("没有差异，不生成升级包")
        return 0

    version = args.version
    if version is None:
        manifest = os.path.join(args.build, "manifest.json")
        version = "unknown"
        if os.path.isfile(manifest):
            with open(manifest, encoding="utf-8") as stream:
                version = json.load(stream).get("version", "unknown")
    zip_name = "Aoi_Tori_CN_Patch_v%s-update.zip" % version
    out_path = os.path.join(args.out_dir, zip_name)

    if args.dry_run:
        print("dry-run：将写入 %s（%d 个文件）" % (out_path, len(changed)))
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
    notes = _notes(changed, version)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for rel in changed:
            archive.write(os.path.join(args.build, *rel.split("/")), rel)
        archive.writestr("升级说明.txt", notes)

    # 校验：包内每个条目必须与 patch/ 里的完全一致
    with zipfile.ZipFile(out_path) as archive:
        for rel in changed:
            digest = hashlib.sha256(archive.read(rel)).hexdigest()
            if digest != built[rel]:
                raise SystemExit("校验失败：%s 打包内容与源不一致" % rel)
        if "升级说明.txt" not in archive.namelist():
            raise SystemExit("校验失败：缺少升级说明")

    size = os.path.getsize(out_path)
    print("已生成 %s" % out_path)
    print("  %d 个文件，%.2f MiB（完整包为 %.1f MiB）"
          % (len(changed), size / 1048576.0,
             sum(os.path.getsize(os.path.join(args.build, *k.split("/")))
                 for k in built) / 1048576.0))
    return 0


def _notes(changed, version):
    """升级说明：只讲怎么用，不复述文件清单。"""
    scripts = [k for k in changed if k.startswith("data/update/")]
    fonts = [k for k in changed if k.startswith("cnpatch/font/")]
    others = [k for k in changed if k not in scripts and k not in fonts]
    lines = [
        "Aoi Tori 简体中文补丁 v%s —— 最小升级包" % version,
        "",
        "本包只包含相对上一版**有变化**的文件，适用于已经装过本补丁旧版本的用户。",
        "首次安装请用完整包，不要用这个。",
        "",
        "## 安装",
        "",
        "1. 解压，把里面的文件和文件夹整体拖进游戏目录，覆盖同名文件。",
        "2. 从 Steam 正常启动游戏即可，不需要改配置、不需要重新设置字体。",
        "",
        "## 本版变化",
        "",
    ]
    if scripts:
        lines.append("* 剧情脚本 %d 份：补译汉化组遗漏的未译条目" % len(scripts))
    if fonts:
        lines.append("* 随包字体 %d 个：按补译后的实际用字重新子集化" % len(fonts))
    for key in others:
        lines.append("* %s" % key)
    lines += [
        "",
        "## 卸载 / 回退",
        "",
        "与完整包一致：删除 cnpatch/ 与 data/update/、data/image/update/ 下本补丁的文件，",
        "再在 Steam 里验证游戏完整性即可恢复原状。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
