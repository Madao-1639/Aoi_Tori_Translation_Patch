# Aoi Tori 简中补丁（Steam 版）

把《Aoi Tori》（Steam appid 2741260）做成**简体中文**，且**不替换 exe、不改动任何原生文件**——保持 Steam 直启（时长统计、云存档、成就正常）。

文本来源：【天之圣杯汉化组】青鸟汉化补丁（85 份 PS2A 脚本，整脚本替换，非「填英文格」）。

## 安装

1. 确保已按官方要求装好 **AoiToriR18Patchv1.04**（本补丁的中文脚本来自 18+ 内容版本）。
2. 把发布包内容复制进游戏安装目录（Steam 库 → 右键 → 管理 → 浏览本地文件）：
   - `mog3x32.dll`（本补丁的代理）
   - 首次安装时把原生 `mog3x32.dll` **改名**为 `mog3x32_orig.dll`，再用发布包里的 `mog3x32.dll` 顶替
   - `data/update/`、`data/image/update/`、`data/update/font/`（新增文件）
3. 从 Steam 正常启动游戏即可。

## 卸载

删除 `mog3x32.dll`、`data/update/`、`data/image/update/` 中由本补丁新增的文件，再把 `mog3x32_orig.dll` 改回 `mog3x32.dll`（或 Steam「验证游戏文件完整性」还原）。

## 实现原理（速查）

| 项 | 结论 |
|---|---|
| 外挂覆盖 | 引擎 `start.ps3` 把 `data\update\`（脚本槽 0）与 `data\image\update\`（图片槽 0）挂在最高优先级，查找从槽 0 开始、首个命中即胜；原生文件无需改动 |
| 脚本容器 | PS2A：`"PS2A" + 48 字节头 + 字节变换 + 可选 LZSS`；`compressed_size=0` 时引擎跳过 LZSS |
| 中文字形 | 引擎 `GetGlyphOutlineA` 的转码完全由 `LOGFONT.charset` 决定（`0x80`=SJIS / `0x86`=GB2312）；exe 里**没有** `MultiByteToWideChar`、`AddFontResource` |
| 引导字节 | 双字节判定内联 9 份（`cmp X,0x9F`），需 `0x9F→0xFE` 才认 GBK |
| 生效通路 | 代理 `mog3x32.dll`（引擎自带 OGG 音频库，19 个导出，静态导入）→ `DllMain` 打内存补丁 + `AddFontResourceExW(FR_PRIVATE)` 私有注册随包字体 |
| 字体 | 随包 OFL 字体，私有加载，只对本进程可见，**免安装**；`cmvs.cfg` 的 `FONT=` 改不动（退出会被写回） |

细节（IDA 地址、跳表、实测数据）见 [`doc/engine-mechanics.md`](doc/engine-mechanics.md)。

## 仓库结构

```
doc/engine-mechanics.md   引擎机制、地址、方案（结论唯一来源）
tool/ps3.py               PS2A 编解码（含 LZSS）
tool/longinus.py          Longinus.dat 读取 / 中文脚本镜像解析
script/extract_patch.py   从 Longinus.dat 提取 85 份中文脚本 + 校验
script/build_patch.py     组装交付目录 patch/ 与 manifest
patch/dll/                代理 DLL（C，m2w64 -m32 构建）
patch/font/               随包字体（子集化产物，不入库）
resource/                 提取产物（不入库，可由脚本重建）
```

## 重建

```bash
python script/extract_patch.py --patch "<汉化补丁>/Longinus.dat" \
    --game "<游戏目录>" --out resource/patch
python script/build_patch.py
```

## 已核实范围 / 未完成

已核实：外挂槽位与查找顺序、PS2A 编解码（85/85 往返一致）、charset 与 9 处引导字节的机制、代理 DLL 可构建（PE32）。
未完成：代理 DLL 的补丁与字体注册实现、随包字体子集化、PB3B 界面图编码器、实机验收。
