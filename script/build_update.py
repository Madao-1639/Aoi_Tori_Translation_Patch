"""生成最小升级包：只打包相对上一版有变化的文件。

全量包不需要脚本 —— `patch/` 就是全量内容，直接 zip 即可（见 README「发布」）。
本脚本只解决一件事：算出「相对上一版哪些文件变了」。

基线是**上一版的 manifest.json**，从 `--baseline` 指定的目录读（默认
`patch/meta/<版本>/manifest.json`）。做法是在组装新版本**之前**把当前
`patch/manifest.json` 另存一份：

    python script/build_patch.py --version 1.0.0
    mkdir -p patch/meta/v1.0.0 && cp patch/manifest.json patch/meta/v1.0.0/

组装完新版再出升级包：

    python script/build_patch.py --version 1.0.1
    python script/build_update.py --from 1.0.0

为什么不用 `releases/` 里的旧 zip 当基线：那个目录不入库、随时会被清理，
拿它当基线等于升级包不可复现。manifest.json 记录每个文件的 `path + bytes + sha256`，
体积几十 KB，留档成本可忽略。

用法：
    python script/build_update.py --from 1.0.0
    python script/build_update.py --from 1.0.0 --baseline <含 manifest.json 的目录>
    python script/build_update.py --from 1.0.0 --dry-run

输出：
    releases/MinimalUpdate_v<上一版>-to-v<当前版本>.zip
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest(path):
    """读 manifest.json，返回 ({相对路径: 条目}, version)。"""
    if not os.path.isfile(path):
        raise SystemExit("找不到清单：%s" % path)
    with open(path, encoding="utf-8") as stream:
        data = json.load(stream)
    entries = {entry["path"]: entry for entry in data.get("files", [])}
    if not entries:
        raise SystemExit("清单里没有 files：%s" % path)
    return entries, data.get("version")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="baseline_version", required=True,
                        help="基线版本号（仅用于命名与提示）")
    parser.add_argument("--baseline", default=None,
                        help="上一版 manifest.json 所在目录，默认 patch/meta/v<版本>")
    parser.add_argument("--build", default=os.path.join(ROOT, "patch"))
    parser.add_argument("--out-dir", default=os.path.join(ROOT, "releases"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    base_dir = args.baseline or os.path.join(args.build, "meta", "v%s" % args.baseline_version)
    baseline, base_version = load_manifest(os.path.join(base_dir, "manifest.json"))
    built, version = load_manifest(os.path.join(args.build, "manifest.json"))
    if not version:
        raise SystemExit("patch/manifest.json 缺 version，先跑 build_patch.py --version")

    added = sorted(k for k in built if k not in baseline)
    changed = sorted(k for k in built
                     if k in baseline and built[k]["sha256"] != baseline[k]["sha256"])
    removed = sorted(k for k in baseline if k not in built)
    payload = added + changed

    print("基线 v%s（%d 个文件） -> 当前 v%s（%d 个文件）"
          % (base_version or args.baseline_version, len(baseline), version, len(built)))
    print("  内容变化：%d" % len(changed))
    for key in changed:
        print("     %s" % key)
    if added:
        print("  新增：%d" % len(added))
        for key in added:
            print("     %s" % key)
    if removed:
        print("  新版不再提供（升级包不负责删除）：%d" % len(removed))
        for key in removed[:10]:
            print("     %s" % key)

    if not payload:
        print("没有差异，不生成升级包")
        return 0

    # 当前构建的文件必须与清单一致，避免拿旧清单配新文件
    for key in payload:
        full = os.path.join(args.build, *key.split("/"))
        if not os.path.isfile(full):
            raise SystemExit("清单里有但磁盘上没有：%s" % key)
        if sha256_file(full) != built[key]["sha256"]:
            raise SystemExit("%s 的 SHA-256 与清单不符，先重跑 build_patch.py" % key)

    zip_name = "MinimalUpdate_v%s-to-v%s.zip" % (args.baseline_version, version)
    out_path = os.path.join(args.out_dir, zip_name)
    total = sum(built[k]["bytes"] for k in payload)
    if args.dry_run:
        print("dry-run：将写入 %s（%d 个文件，%.2f MiB）"
              % (out_path, len(payload), total / 1048576.0))
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for rel in payload:
            archive.write(os.path.join(args.build, *rel.split("/")), rel)
        archive.writestr("升级说明.txt", _notes(version, args.baseline_version, payload))

    with zipfile.ZipFile(out_path) as archive:
        for rel in payload:
            if hashlib.sha256(archive.read(rel)).hexdigest() != built[rel]["sha256"]:
                raise SystemExit("校验失败：%s 打包内容与清单不一致" % rel)
        if "升级说明.txt" not in archive.namelist():
            raise SystemExit("校验失败：缺少升级说明")

    size = os.path.getsize(out_path)
    print("已生成 %s" % out_path)
    print("  %d 个文件，%.2f MiB（未压缩 %.1f MiB）"
          % (len(payload), size / 1048576.0, total / 1048576.0))
    return 0


def _notes(version, base_version, payload):
    scripts = [k for k in payload if k.startswith("data/update/")]
    fonts = [k for k in payload if k.startswith("cnpatch/font/")]
    others = [k for k in payload if k not in scripts and k not in fonts]
    lines = [
        "Aoi Tori 简体中文补丁 v%s —— 相对 v%s 的最小升级包" % (version, base_version),
        "",
        "本包只包含有变化的文件，适用于已经装过 v%s 的用户。" % base_version,
        "首次安装请下载完整包，不要用这个。",
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
        lines.append("* 剧情脚本 %d 份" % len(scripts))
    if fonts:
        lines.append("* 随包字体 %d 个（按实际用字重新子集化）" % len(fonts))
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
