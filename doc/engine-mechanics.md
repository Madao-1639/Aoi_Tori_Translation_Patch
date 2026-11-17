# 引擎机制与汉化方案

本文件只写**当前成立**的结论，每条都带证据（IDA 地址 / 复跑命令）。
样本：Steam 版《Aoi Tori》，`cmvs32.exe` SHA-256 `3a2ec3af6a16a4f36d72faf9e7cb14e2bcc883fbf22fb31da5d69434cc53727e`，
image base `0x400000`（PE32，`cmvs32[ver.3.80]`，`ps3[ver.1.00]`）。
游戏当前处于「已装 R18 补丁」状态（`data/code` 86 份脚本、`data/pack/{stand,up,balloon}.cpz`、`data/image/update` 1701 张图均为 R18 补丁产物）。

## 1. 文件查找：引擎自带「外挂覆盖」槽位

`start.ps3` 在启动时把若干根目录/封包挂进若干**路径表**，每张表有固定槽位数；查找时**从槽 0 开始、首个命中即返回**（`sub_46CB80` 脚本用，`sub_43FEA0` / `sub_440270` 图片用）。表项结构为 `[2048 字节路径][4 字节 pack 对象指针]`（`sub_48A930`，`imul esi, eax, 804h`）：路径以 `\` 结尾即**目录**（直接开磁盘文件 `sub_419870`），否则是**封包**（走 pack 对象 `vtable+0x0C`）。

挂载命令在系统命令分发表 `sub_4729F0`（跳表 `jpt_472A01`，`case N` → 命令号 `0x2000+N`）：

| 命令 | 处理器 | 目标 |
|---|---|---|
| `0x2010` | `sub_47B710` | 脚本路径表 `engine+0xD08`（2 参数：路径、槽） |
| `0x2011` | `sub_47B200` | 图片管理器 `sub_440270`（10 槽） |
| `0x2012` | `sub_47B9B0` | 路径表 `engine+0xD0C` |
| `0x2013` | `sub_47B460` | 路径表 `engine+0xD10` |
| `0x2014` | `sub_47B880` | 路径表 `engine+0xD14` |
| `0x2019` | `sub_47B790` | 路径表（7 槽） |
| `0x201a` | `sub_47B330` | 路径表 |
| `0x2268`–`0x226e` | `sub_47B660/B150/B900/B3B0/B7D0/B4E0/B280` | 同上各表，6 参数版（额外 4 个 CPZ 密钥字） |

从 `start.ps3` 解出的实际挂载序（`resource/patch` 工具可复现：`tool/ps3.py` + 脚本反汇编）：

| 资源族 | 槽 0 | 槽 1 | 槽 2 … |
|---|---|---|---|
| 脚本 `0x2010` | **`data\update\`** | `data\code\` | — |
| 图片 `0x2011` | **`data\image\update\`** | `data\image\chip\` | `bg.cpz`(2)、`event\`(3)、`event2\`(4)、`stand.cpz`(5)、`up.cpz`(6)、`balloon.cpz`(7)、`event3\`(8) |
| 声音 `0x2014` | `data\image\update\` | `voice2.cpz` | `voice.cpz` |
| 音乐/视频/存档 | `data\music\`、`data\video\`、`save\` | — | 各自独立表 |

**结论（外挂机制）**：把 `*.ps3` 放进 `data/update/`、把 `*.pb3` 放进 `data/image/update/`，即可覆盖同名的 `data/code` 与各封包内容，**原生文件一个字节都不用改**。R18 官方补丁就是靠这两个目录做的覆盖（`data/image/update` 1701 个 `PB3B`）。

`data/image/{chip,event,update}` 下的图片是**扁平**文件名（`cg_bg.pb3`、`ev_ak01a.pb3`…），即 `data/image/update/cg_bg.pb3` 就能压过 `chip` 或 `bg.cpz` 里的同名片。

## 2. 脚本容器 PS2A（`.ps3`）

```
+0   "PS2A"
+4   u32[11]: 48, version_raw, key, label_count, code_size,
             unknown_size, text_size, entry_pc, compressed_size, decoded_size, reserved
+48  载荷：labels(label_count*4) | code | unknown | text
```
- 载荷先做字节变换：`plain = ror8(((enc-124)&0xFF) ^ xor, shift)`，`shift = (key>>20)%5+1`，`xor = ((key>>3)+(key>>24))&0xFF`（`sub_46CB80`）。
- `compressed_size != 0` 时再做 2048 环形窗口 LZSS（写指针从 `0x7DF` 起，flag 低位先，`1`=字面，引用 `offset = b1 | ((b2&0xE0)<<3)`、`length = (b2&0x1F)+2`）（`sub_46FEA0`）。
- **`compressed_size = 0` 时引擎完全跳过 LZSS**，补丁脚本可以不压缩（工具见 `tool/ps3.py`，已验证 85 份脚本 encode/decode 往返一致）。
- 字符串引用 = `text` 段内**字节偏移**，表达式形态 `[01 02 20 01][u32 offset][0f 02]`（另见 §5）。

## 3. 文本渲染：唯一的硬阻碍

- 字体创建只有 `CreateFontA`（3 个函数 4 个调用点：`sub_4618E0`/`sub_461960`×2/`sub_461A90`）。`iCharSet` 只有两档：`sub_419A50()`（= `initial.cfg` 的 `FONT_SHIFT_JIS`）非 0 → `0x80`(SHIFTJIS)，否则 `0x00`(ANSI)；`push 80h` 立即数在 `0x46191A / 0x4619F9 / 0x461A45 / 0x461AE0`。
- 字形来自 `GetGlyphOutlineA`（`sub_4613B0`）：引擎自己把字节合成 16 位码，再交给 GDI，**转码完全由 LOGFONT.charset 决定**（ctypes 实测：charset `0x86` + `0xC4E3` 与 `GetGlyphOutlineW(0x4F60)` 输出逐字节相同；charset `0x80` + `0xF040` = `W(0xE000)`）。
- exe 内**没有** `MultiByteToWideChar` / `AddFontResource*`；`SetWindowTextA` 等一律 ANSI。
- 双字节判定内联了 9 份（形状 `cmp X,0x81 / jb / cmp X,0x9F / jbe`，另一支用 `lea t,[X+0x20]; cmp t,0x1C` 判 `0xE0-0xFC`）。本作 9 处立即数字节：

  `0x4182F9 0x419D39 0x41A04B 0x41A089 0x41A0D6 0x41A146 0x41A1B9 0x41A2CB 0x41A3A9`

  （`3C 9F` 与 `80 F? 9F` 两种编码混排，必须按**立即数字节**打，不能按指令地址。）
- 因此 GBK 文本必须：① charset `0x80→0x86`（GB2312）；② 9 处 `0x9F→0xFE`。

**没有任何配置项或脚本命令能改变 charset**（`FONT_SHIFT_JIS` 只区分 0/非 0；`sub_419D70` 那个可被脚本命令 `sub_486C40` 切换的标志是「双字节排版模式」，与 charset 无关）。所以中文渲染**必须改代码**。

## 4. 生效通路：代理 `mog3x32.dll`（不改 exe）

- `mog3x32.dll`（321 KB，PE32，仅依赖 KERNEL32）是 Purple 的 OGG/MOG 音频封装库，导出 19 个符号（`MOG_Init/InputFile/GetPcmFormat/GetProcessFrame/Replay…`）。exe 的导入表**静态**引用它，且它在游戏目录 → Windows 与 Wine 都优先从 exe 目录加载。
- 做法：原文件改名 `mog3x32_orig.dll`（内容不变），放我们的同名代理：19 个导出用裸 `jmp [ptr]` 转发到原 DLL，`DllMain(PROCESS_ATTACH)` 里打内存补丁 + 私有注册字体。
- 好处：Steam 直接启动 `cmvs32.exe`（时长统计/云存档/成就正常）；无启动器 exe；字体免安装（`AddFontResourceExW(..., FR_PRIVATE, 0)`，只对本进程可见，`EnumFontFamiliesExA` 可枚举到）；卸载 = 删文件 + 改回原名。
- 构建：本机 `condaenvs/adhdhook` 的 m2w64（`x86_64-w64-mingw32-gcc` 5.3.0）可用 `-m32` 出 `pe-i386`，再用 `ld -m i386pe --dll` 链接；不依赖 CRT/导入库（自行解 PEB 取 kernel32/gdi32 导出）。已验证最小代理 DLL 可产出 PE32。

## 5. 汉化素材：CN_patch 已是完整中文脚本

`CN_patch/Longinus.dat`：`"Longinus\x01\x04\x00\x01" + u32 条目数 + {name_hash, size, offset}`，`name_hash = Σ(utf16le(name.upper())) h=h*131+b，取 &0x7FFFFFFF`。137 项 = 85 份 PS2A（**已解码的内存镜像**：头保留原 key/compressed_size，载荷是明文）+ 52 张 BGRA 原图。

- 85 份脚本覆盖除 `intcode.ps3` 外的全部脚本，文件名可用 `name_hash` 对 `data/code/*.ps3` 反查（零未映射）。
- 译文是**整脚本替换**（不是往英文格里填字）：字符串条目数与英文版几乎一一对应（`sn101`：4795 vs 4810），开篇逐句对应，因此不存在「按英文行数硬塞」的问题；行数差异由脚本自身承载。
- 提取/校验：`script/extract_patch.py`（85/85 往返一致，报告 `resource/patch/report.json`）。
- 39/52 张图能按脚本里出现的 `.pb3` 名字反查（其余 13 张多为 `eyecatch`/标题底图，需从封包成员名反查，见 §7）。

## 6. 交付形态

```
<游戏目录>/
  mog3x32.dll          代理（转发 19 导出 + 内存补丁 + 私有字体注册）
  mog3x32_orig.dll     原文件改名（内容不变）
  data/update/*.ps3    85 份中文脚本（覆盖 data/code）
  data/update/font/AoiToriCN.ttf   随包字体（OFL，按用字子集化）
  data/image/update/*.pb3          界面图（PB3B，待做）
```

## 7. 待办与风险

- 图片 13/52 未定位到文件名；需解析 `*.cpz`（CPZ7，密钥来自 `start.ps3` 的 `0x2268`–`0x226e` 参数）或从 `data/image/*` 已有文件名单匹配。
- 界面图需要 **PB3B 编码器**（type 1，纯字面 LZSS 最省事）；解码规则见 `docs/formats/pb3-format.md`（cmvs-rs）。头部 8..52 与文件末尾 -47/-3/-2 字节交替变换，编码时用真实尾部字节反算即可。
- `全角空格` 开头文本可能把文本框算成 0 宽（同引擎《ハピメア》补丁实测），需在构建期 strip：要求「重建 text 段 + 重定向字符串偏移」的工具（§2 的引用形态全部覆盖）。
- `♪` 不在 GBK，需哨兵字节 + hook `GetGlyphOutlineA` 转 `GetGlyphOutlineW`（可选）。
- `CreateFontA` 4 个调用点是否全改 charset 会影响排版宽度（`tmMaxCharWidth` 40 vs 79），需实测；参照《ハピメア》只改正文那一处。
- 字体默认名不要指望 `cmvs.cfg FONT=`（游戏退出会用内存值写回覆盖），应在 `CreateFontA` hook 里改 face，或改内存里的默认串。

## 8. 验证记录（2026-11-17，实机）

安装形态：原生 `mog3x32.dll` 改名 `mog3x32.dll.orig`；代理 `mog3x32.dll` + `data/update/cnpatch.dll` + 85 份中文脚本。

`data/update/cnpatch.log`：

```
init	exe=00400000 kernel32=76a50000 gdi32=758a0000
image	base=00400000 size=00151000
lead-byte	applied 9/9
iat	CreateFontA hook ok, original=758a5330
font-file	...\data\update\font\AoiToriCN.ttf -> 0     ← 随包字体尚未提供
font	h=0 w=400 charset 80->86 face=MS Gothic
font	h=-28 w=400 charset 80->86 face=MS Gothic        ← 正文
```

实机结果：Steam 直启 `cmvs32.exe`（未替换 exe），标题画面正常，进入剧情后中文正常显示与换行：

> 「我要把那象征着幸福的青鸟，从美丽的苍穹一把给拍落在地，囚禁在笼子里」

即：代理注入 + 9 处引导字节 + charset 钩子 + `data/update` 脚本覆盖这条链路**端到端打通**。
此时字体仍走 GDI 按 charset 134 自动替换（`MS Gothic` 无中文字形 → 系统替换为新宋体），说明"只改 charset、不改字体名"也能显示中文；随包字体只是为了让字形与度量可控、且免安装。

### 开发期踩到的两个工具链坑（已修）

1. 本机 m2w64 的 `ld -m i386pe` 产出的 DLL **没有 `.reloc`**（`--dynamicbase` 也不生成）。DLL 首选基址被占用时 `LoadLibrary` 会以 `ERROR_INVALID_ADDRESS (487)` 失败。
   → `build.sh` 给 `cnpatch.dll` 指定 `--image-base 0x6A000000`，与代理的 `0x10000000` 错开。
2. **不要在 `DllMain` 里 `LoadLibrary` 补丁本体**：此时进程仍在加载器临界区，实测被拒绝（同样是 487）。
   → 代理改为开线程，等 `gdi32.dll` 出现在模块表后再加载 `cnpatch.dll`（实际耗时 <10 ms，远早于引擎建字体）。

## 9. 安装布局（2026-11-17 调整）

只有**引擎会读**的东西才放 `data/`；补丁自身文件放游戏根的自有目录：

```
<游戏目录>/
  mog3x32.dll            代理（名字被 exe 导入表钉死，必须在根目录）
  mog3x32.dll.orig       原生文件改名（内容不变）
  cnpatch/cnpatch.dll    补丁本体（语义明确，与引擎无关）
  cnpatch/font.ttf       随包字体（DLL 私有注册，免安装）
  cnpatch/cnpatch.log    运行日志（开发期诊断）
  data/update/*.ps3      中文脚本（引擎脚本路径表槽 0）
  data/image/update/*.pb3  界面图（引擎图片表槽 0）
```

字体策略：**不改字体名**。`CreateFontA` 钩子只把 charset 改成 `0x86`，face 原样透传（默认 `MS Gothic`），
GDI 按 charset 自行替换成系统中文字体 —— 实测中文显示正常。随包字体只是为了让字形/度量可控且免安装，
其字体族名沿用原名，不需要引擎侧任何改动。

未决：窗口标题仍显示 `Aoi Tori[ver.1.03] R18`（空的 `data/update/start.ps3` 标题是 GBK「青鸟【天之圣杯汉化组】」），
但脚本正文确实来自 `data/update/`，需单独查标题来源（不影响正文）。
