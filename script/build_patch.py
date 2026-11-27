"""Compose the deliverable tree (`patch/`) from extracted resources.

Inputs:
  resource/patch/scripts/*.ps3       中文脚本（script/extract_patch.py 产物）
  resource/patch/pictures_pb3/*.pb3  界面图（script/build_images.py 的 PB3B 产物）
  patch/cnpatch/font/*.ttf           随包字体（script/build_font.py 产物，可放多个）
  patch/mog3x32.dll                  原生音频库 + 引导节（dll/build_loader.sh 产物）

Output: patch/data/update/*.ps3、patch/data/image/update/*.pb3、清单 patch/manifest.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# start.ps3 负责把 data/update 挂到最高优先级，它自己只能来自 data/code，
# 放进 data/update 永远不会被读到（标题串也因此只能靠钩子改）。
SKIP = {"start.ps3"}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def collect(source_dir, pattern, destination_dir, out_root, magic=None):
    entries = []
    if not os.path.isdir(source_dir):
        return entries
    for name in sorted(os.listdir(source_dir)):
        if name in SKIP:
            continue
        if not name.lower().endswith(pattern):
            continue
        src = os.path.join(source_dir, name)
        if magic is not None:
            with open(src, "rb") as stream:
                if stream.read(len(magic)) != magic:
                    print("skip %s: not %s" % (name, magic.decode("ascii")))
                    continue
        os.makedirs(destination_dir, exist_ok=True)
        dst = os.path.join(destination_dir, name)
        shutil.copy2(src, dst)
        entries.append({"path": os.path.relpath(dst, out_root).replace(os.sep, "/"),
                        "bytes": os.path.getsize(dst), "sha256": sha256_file(dst)})
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scripts", default=os.path.join(ROOT, "resource", "patch", "scripts"))
    parser.add_argument("--pictures", default=os.path.join(ROOT, "resource", "patch", "pictures_pb3"))
    parser.add_argument("--out", default=os.path.join(ROOT, "patch"))
    parser.add_argument("--version", default="1.1.0",
                        help="写进 manifest.json，供 script/build_update.py 定升级包文件名")
    args = parser.parse_args()

    scripts = collect(args.scripts, ".ps3", os.path.join(args.out, "data", "update"), args.out)
    pictures = collect(args.pictures, ".pb3", os.path.join(args.out, "data", "image", "update"),
                       args.out, magic=b"PB3B")

    extra = []
    for path in ("mog3x32.dll", "cnpatch/cnpatch.dll"):
        full = os.path.join(args.out, path)
        if os.path.isfile(full):
            extra.append({"path": path.replace(os.sep, "/"), "bytes": os.path.getsize(full),
                          "sha256": sha256_file(full)})
    font_dir = os.path.join(args.out, "cnpatch", "font")   # 目录里可能有多个用户字体
    if os.path.isdir(font_dir):
        for name in sorted(os.listdir(font_dir)):
            full = os.path.join(font_dir, name)
            extra.append({"path": os.path.relpath(full, args.out).replace(os.sep, "/"),
                          "bytes": os.path.getsize(full), "sha256": sha256_file(full)})

    manifest = {
        "version": args.version,
        "scripts": len(scripts), "pictures": len(pictures),
        "files": scripts + pictures + extra,
    }
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    print("scripts %d, pictures %d, extra %d, total %.1f MiB"
          % (len(scripts), len(pictures), len(extra),
             sum(f["bytes"] for f in manifest["files"]) / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
