"""Compose the deliverable tree (`patch/`) from extracted resources.

Inputs:
  resource/patch/scripts/*.ps3       中文脚本（script/extract_patch.py 产物）
  resource/patch/pictures/*.pb3      界面图（可选，待 PB3B 编码器接入）
  patch/font/AoiToriCN.ttf           随包字体（可选）
  patch/dll/mog3x32.dll              代理 DLL（patch/dll/build.sh 产物，可选）

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


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def collect(source_dir, pattern, destination_dir, magic=None):
    entries = []
    if not os.path.isdir(source_dir):
        return entries
    for name in sorted(os.listdir(source_dir)):
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
        entries.append({"path": os.path.relpath(dst, ROOT).replace(os.sep, "/"),
                        "bytes": os.path.getsize(dst), "sha256": sha256_file(dst)})
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scripts", default=os.path.join(ROOT, "resource", "patch", "scripts"))
    parser.add_argument("--pictures", default=os.path.join(ROOT, "resource", "patch", "pictures"))
    parser.add_argument("--out", default=os.path.join(ROOT, "patch"))
    args = parser.parse_args()

    scripts = collect(args.scripts, ".ps3", os.path.join(args.out, "data", "update"))
    pictures = collect(args.pictures, ".pb3", os.path.join(args.out, "data", "image", "update"),
                       magic=b"PB3B")

    extra = []
    for path in ("mog3x32.dll", "data/update/font/AoiToriCN.ttf"):
        full = os.path.join(args.out, path)
        if os.path.isfile(full):
            extra.append({"path": path.replace(os.sep, "/"), "bytes": os.path.getsize(full),
                          "sha256": sha256_file(full)})

    manifest = {
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
