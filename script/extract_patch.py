"""Extract the 天之圣杯汉化组 patch (CN_patch/Longinus.dat) into readable assets.

Inputs:
  --patch   Longinus.dat from the fan patch
  --game    game root (used to name entries: script file names, .pb3 references)

Outputs under --out:
  scripts/<name>.ps3      re-encoded PS2A (uncompressed, original key)
  pictures/<name>.pb3     raw BGRA image bodies whose names could be resolved
  report.json             per-entry verification record

Nothing here writes into the game directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tool"))

import longinus  # noqa: E402
import ps3  # noqa: E402


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def script_candidates(game: str):
    """File names present under data/code (the engine's script directory)."""
    folder = os.path.join(game, "data", "code")
    return [name for name in os.listdir(folder) if name.lower().endswith(".ps3")]


def picture_candidates(scripts):
    """Every .pb3 string referenced by any script, plus directory prefixes."""
    found = set()
    pattern = re.compile(rb"[A-Za-z0-9_\-]{1,40}\.pb3")
    for script in scripts:
        for _, raw in script.strings():
            for match in pattern.findall(raw):
                found.add(match.decode("ascii"))
    return sorted(found)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patch", required=True)
    parser.add_argument("--game", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    patch = longinus.Patch.open(args.patch)
    script_names = {longinus.name_hash(name): name for name in script_candidates(args.game)}

    os.makedirs(os.path.join(args.out, "scripts"), exist_ok=True)
    os.makedirs(os.path.join(args.out, "pictures"), exist_ok=True)

    report = {"patch_sha256": sha256(open(args.patch, "rb").read()), "scripts": [], "pictures": []}
    decoded = {}

    for key, data in sorted(patch.scripts().items()):
        name = script_names.get(key)
        if name is None:
            raise SystemExit("unmapped script hash %08x" % key)
        script = longinus.read_script(data)
        encoded = script.encode()
        again = ps3.Script.decode(encoded)
        if again.payload != script.payload:
            raise SystemExit("round-trip mismatch for %s" % name)
        path = os.path.join(args.out, "scripts", name)
        with open(path, "wb") as stream:
            stream.write(encoded)
        decoded[name] = script
        empty = sum(1 for _, raw in script.strings() if not raw)
        report["scripts"].append({
            "name": name, "hash": "%08x" % key,
            "label_count": script.label_count, "code_size": script.code_size,
            "unknown_size": script.unknown_size, "text_size": script.text_size,
            "strings": len(script.strings()), "empty_strings": empty,
            "source_bytes": len(data), "output_bytes": len(encoded),
            "payload_sha256": sha256(script.payload),
        })

    names = picture_candidates(decoded.values())
    by_hash = {longinus.name_hash(name): name for name in names}
    for key, data in sorted(patch.pictures().items()):
        name = by_hash.get(key)
        record = {"hash": "%08x" % key, "bytes": len(data), "name": name}
        if name:
            path = os.path.join(args.out, "pictures", name)
            with open(path, "wb") as stream:
                stream.write(data)
            record["pixels"] = [struct.unpack_from("<I", data, 0)[0] % 65536]
        else:
            record["pixels"] = [struct.unpack_from("<I", data, 0)[0] % 65536]
        report["pictures"].append(record)

    with open(os.path.join(args.out, "report.json"), "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")

    unresolved = [r for r in report["pictures"] if not r["name"]]
    print("scripts: %d written, %d verified" % (len(report["scripts"]), len(report["scripts"])))
    print("pictures: %d found, %d named, %d unresolved"
          % (len(report["pictures"]), len(report["pictures"]) - len(unresolved), len(unresolved)))
    for item in unresolved[:10]:
        print("  unresolved %s (%d bytes)" % (item["hash"], item["bytes"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
