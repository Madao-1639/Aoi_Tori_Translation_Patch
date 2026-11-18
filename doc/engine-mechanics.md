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

## 3. 图片容器 PB3B（`.pb3`，type 1）

界面贴图是 PB3B 容器，头部 8..52 字节加密，之后是「flags 区」「data 区」两个独立区：

```
+0   "PB3B"
+8   u32 field08（保留原值即可）
+24  u32 分块边长（实测均为 16）
+28  u16 codec 类型（1 = 平面分离 + 分块标记 + LZSS）
+30  u16 宽 / +32 u16 高 / +34 u16 位深（24 或 32）
+44  u32 flags 区偏移 / +48 u32 data 区偏移
头部 8..52 共 44 字节：enc[i] = (plain[i] + raw[len-47+i]) ^ raw[len-3+(i%2)]
```

- 两个区都先放「每通道一个 u32 长度」表，再按 **B、G、R、(A)** 顺序连着放各通道块。
- flags 区每通道：`u32 分块标记字节数`、`u32 整块同值的块数`、`u32 字面像素数`，随后是分块标记（高位先，`1`=整块同值）、每块一个的常量值、LZSS 位流。
- data 区每通道：字面像素字节。位流 `1`=引用、`0`=字面，8 个 token 一个字节；整字节为 0 且正好剩满 8 个 token 时走「直接拷 8 字节」的快路径。
- 引用 16 位：`offset = w >> 5`（≤2047）、`length = (w & 31) + 3`（以 `cmvs-core/src/pb3.rs` 为准）。
- **字面像素是「分块优先」序**：`for y块 { for x块 { for 块内行 } }` 逐行取 `columns` 字节，不是整幅光栅序。编码器踩过一次，症状是首个分块之后的像素整体错位。
- 引擎不校验 checksum，也可以全走字面，所以最省事的编码器 = 「分块标记全 0 + 位流全 0 + 全量字面」，体积约为原图 1.8 倍，正确性优先。工具：`tool/pb3.py`；实测 5 份游戏原图解码成功，自造图与真实图往返**逐像素一致**。

### 位深：24 与 32 并存，替换时必须与原图一致

`+34` 的位深有两种，两种都常见（本作文档目录：3548 张图里 3174 张 32 位、364 张 24 位）：

| 位深 | 通道 | 分布（本作） | 典型用途 |
|---|---|---|---|
| 32 | B,G,R,**A** | 3174 张 | 界面贴图、需要透明叠加的图 |
| 24 | B,G,R | 364 张 | 背景、遮罩、特效、过场（本来就没有 alpha 语义） |

- **解码**：24 位图只有 3 个通道块，解码后需补 `alpha = 255` 才能当 RGBA 用。
- **编码**：必须按**原图的位深**输出。把 32 位数据放到原本 24 位的位置，会多写一个通道块，引擎按 24 位解析就通道错位——症状是画面偏色或出现竖条。反之亦然。
- **编码器的接口约定**：输入恒为 4 字节/像素的 RGBA，输出通道数由 `bpp` 决定（24 时丢弃 alpha）。这样调用方不必关心位深差异，转换只发生在编码器内部。

> 踩过的坑：早期编码器写死 32 位，`script/build_images.py` 又额外校验「原图必须 32bpp」，导致 24 位原图对应的汉化图被整批跳过（本作 5 张 `ev_etc06a~e`）。筛选条件应该只校「尺寸匹配」，位深由编码器按原图决定。

## 4. 文本渲染：唯一的硬阻碍

- 字体创建只有 `CreateFontA`（3 个函数 4 个调用点：`sub_4618E0`/`sub_461960`×2/`sub_461A90`）。`iCharSet` 只有两档：`sub_419A50()`（= `initial.cfg` 的 `FONT_SHIFT_JIS`）非 0 → `0x80`(SHIFTJIS)，否则 `0x00`(ANSI)；`push 80h` 立即数在 `0x46191A / 0x4619F9 / 0x461A45 / 0x461AE0`。
- 字形来自 `GetGlyphOutlineA`（`sub_4613B0`）：引擎自己把字节合成 16 位码，再交给 GDI，**转码完全由 LOGFONT.charset 决定**（ctypes 实测：charset `0x86` + `0xC4E3` 与 `GetGlyphOutlineW(0x4F60)` 输出逐字节相同；charset `0x80` + `0xF040` = `W(0xE000)`）。
- exe 内**没有** `MultiByteToWideChar` / `AddFontResource*`；`SetWindowTextA` 等一律 ANSI。
- 双字节判定内联了 9 份（形状 `cmp X,0x81 / jb / cmp X,0x9F / jbe`，另一支用 `lea t,[X+0x20]; cmp t,0x1C` 判 `0xE0-0xFC`）。本作 9 处立即数字节：

  `0x4182F9 0x419D39 0x41A04B 0x41A089 0x41A0D6 0x41A146 0x41A1B9 0x41A2CB 0x41A3A9`

  （`3C 9F` 与 `80 F? 9F` 两种编码混排，必须按**立即数字节**打，不能按指令地址。）
- 因此 GBK 文本必须：① charset `0x80→0x86`（GB2312）；② 9 处 `0x9F→0xFE`。

**没有任何配置项或脚本命令能改变 charset**（`FONT_SHIFT_JIS` 只区分 0/非 0；`sub_419D70` 那个可被脚本命令 `sub_486C40` 切换的标志是「双字节排版模式」，与 charset 无关）。所以中文渲染**必须改代码**。

## 5. 生效通路：改 `mog3x32.dll`（不改 exe）

中文所需的三处改动（charset、双字节判定、标点禁则）全在 `cmvs32.exe` 里，靠往 `data/` 放文件解决不了。改 exe 就失去 Steam 直启，所以只能进程内注入——而注入需要改一个被静态导入的 DLL。

- `mog3x32.dll`（321 KB，PE32，仅依赖 KERNEL32）是 Purple 的 OGG/MOG 音频封装库，导出 19 个符号（`MOG_Init/InputFile/GetPcmFormat/GetProcessFrame/Replay…`）。exe 的导入表**静态**引用它，且它在游戏目录 → Windows 与 Wine 都优先从 exe 目录加载。这是唯一同时满足「静态导入」与「两平台都从 exe 目录加载」的文件。
- 做法：**在原生 DLL 上追加代码**，原有的节、导出表、19 个导出的实现全部原地保留，只加一个新节放引导代码（`DllMain` 里开线程等 `gdi32` 就绪，再 `LoadLibrary` 补丁本体）。
- 为什么不采用「替换 + 转发」：那需要把原文件改名成 `.orig`、再让代理转发 19 个导出。多一个文件、多一层 `jmp`，且把 `.orig` 变成必需依赖（缺了就没音频）。直接追加没有这些代价，原生代码也根本没被改过。
- 好处：Steam 直接启动 `cmvs32.exe`（时长统计/云存档/成就正常）；无启动器 exe；字体免安装（`AddFontResourceEx(..., FR_PRIVATE, 0)`，只对本进程可见，`EnumFontFamiliesExA` 可枚举到）；恢复原生状态 = Steam 验证游戏完整性。
- 构建：`<m2w64>/bin` 的 `x86_64-w64-mingw32-gcc` 5.3.0 可用 `-m32` 出 `pe-i386`，再用 `ld -m i386pe --dll` 链接；不依赖 CRT/导入库（自行解 PEB 取 kernel32/gdi32 导出）。

### 标题栏（`SetWindowTextA` 钩子）

标题串固定在 `data/code/start.ps3`（`data/update/` 正是它自己挂上去的，所以放进 `data/update/` 的 `start.ps3` 永远不会被读到），内容形如 `Aoi Tori[ver.1.03] R18` + SJIS 全角空格 `81 40` + `- 章节名 -`。GBK 模式下 `81 40` 解成「号」，历史截图里的 `R18号` 即由此而来。
钩子做两件事：前缀换成 GBK 的「青鸟【简体中文】」，`81 40` 换成 GBK 全角空格 `A1 A1`（等长，不动其他字节）。

## 6. 汉化素材：CN_patch 已是完整中文脚本

`CN_patch/Longinus.dat`：`"Longinus\x01\x04\x00\x01" + u32 条目数 + {name_hash, size, offset}`，`name_hash = Σ(utf16le(name.upper())) h=h*131+b，取 &0x7FFFFFFF`。137 项 = 85 份 PS2A（**已解码的内存镜像**：头保留原 key/compressed_size，载荷是明文）+ 52 张 BGRA 原图。

- 85 份脚本覆盖除 `intcode.ps3` 外的全部脚本，文件名可用 `name_hash` 对 `data/code/*.ps3` 反查（零未映射）。其中 `start.ps3` 因自举顺序永远读不到（§5），实际交付 84 份。
- 译文是**整脚本替换**（不是往英文格里填字）：字符串条目数与英文版几乎一一对应（`sn101`：4795 vs 4810），开篇逐句对应，因此不存在「按英文行数硬塞」的问题；行数差异由脚本自身承载。
- 提取/校验：`script/extract_patch.py`（85/85 往返一致，报告 `resource/patch/report.json`）。
- 52 张图里 39 张能按脚本里出现的 `.pb3` 名字反查（其余 13 张多为 `eyecatch`/标题底图，需从封包成员名反查，见 §8）；已编码交付 38 张（`message_log2` 分辨率不符跳过）。

## 7. 交付形态与安装布局

只有**引擎会读**的东西才放 `data/`；补丁自身文件放游戏根的自有目录：

```
<游戏目录>/
  mog3x32.dll              原生音频库 + 追加的引导节（唯一被改动的官方文件）
  cnpatch/cnpatch.dll      补丁本体（语义明确，与引擎无关）
  cnpatch/font/*.ttf       随包字体（思源黑体 Medium/Bold；玩家可自行增减，只注册 .ttf）
  data/update/*.ps3        中文脚本 84 份（引擎脚本路径表槽 0；start.ps3 排除，见 §5）
  data/image/update/*.pb3  界面图 38 份（引擎图片表槽 0，PB3B type 1）
```

交付体积大头是界面贴图（当前全字面编码；接 LZSS 后可显著压缩）。实际数值随素材与编码策略变化，不在此记录。

**版本闸门**：9 处引导字节必须全部命中才启用其余改动；任一不中 → `cnpatch.log` 记
`lead-byte applied N/9 -> DISABLED`，字体注册与 charset 钩子一并跳过，游戏按原版行为运行。

## 8. 待办与风险

- **交付体积**：PB3B 目前「分块标记全 0 + 位流全 0 + 全量字面」，体积约为原图的 1.8 倍。接 LZSS 需要写匹配器
  （`offset = w >> 5` ≤ 2047、`length = (w & 31) + 3`），预期可回到接近原图体积。
- **13 张图片未定位**：需解析 `*.cpz`（CPZ7，密钥来自 `start.ps3` 的 `0x2268`–`0x226e` 参数）或与 `data/image/*` 已有文件名匹配。
- **`message_log2.pb3` 跳过**：汉化组裸图 1536×1024，游戏原图 1536×2048（疑似两页竖排），直接替换会错位。
- **`intcode.ps3` 不汉化**：汉化组的 85 份脚本 = 除它之外全部；它是纯 ASCII 的内部调试标签，无乱码可修（见 `lessons-learned.md` §12）。
- **全流程抽验**：逐章进度、存读档/回想/鉴赏、各结局分支，目前只抽验了标题画面与开场剧情。
- **`config02_chip.pb3` 素材缺口已补**：汉化组图集里 `自动读取` 与 `文字渐隐` 之间为空（原版为 `Next Voice`）。
  该格已人工补图，源文件与生成参数见 `resource/ui/README.md`。

## 9. 验证记录（实机，Steam 直启）

`cnpatch/cnpatch.log`（当前版本）：

```
init	exe=00400000 kernel32=76a50000 gdi32=758a0000
image	base=00400000 size=00151000
lead-byte	applied 9/9
iat	SetWindowTextA ok EnumFontFamiliesExA ok
iat	CreateFontA hook ok, original=758a5330
font-default	patched 5/5 -> 思源黑体 (4d0bf8 def, 4e9298, 4d1ad4, 4e9670, 4e97b0)
font-dir	registered 2 failed 0
font-enum	charset134 count=119 ours=FOUND
font	h=-28 w=400 charset 80->86 face=思源黑体
title	-> 青鸟【简体中文】　- 标题画面 -
```

实机现象：

- 剧情对白中文正常显示与换行：「正因为如此，我不会再舍弃任何人。」
- 标题栏：`青鸟【简体中文】 - 标题画面 -`、`青鸟【简体中文】 - 伊始的三天[1/3] -`
- 标题菜单全部中文贴图：开始游戏 / 明理篇 / 继续游戏 / 鉴赏模式 / 系统设定 / 结束游戏
- 设置页字体列表出现 `思源黑体`（`charset 134`，`fonttype=4`），字体框默认值即 `思源黑体`（无钩子，来自引擎默认串）
- 设置页整页中文：系统设定 / 其他设定 / 角色个人设定、声音回放速度 / 音量 / 原文表示速度 / 自动读取 / 未读·已读原文、结束游戏 / 返回

## 10. 随包字体：目录化注册 + 改默认值

### 10.1 字体目录（用户可自行扩展）

`cnpatch/font/` 下的 `.ttf` 会被 `FindFirstFileA` 扫到并逐个
`AddFontResourceEx(path, FR_PRIVATE, 0)` 私有注册（日志 `font-dir registered N failed M`）。
注册**不需要知道族名** —— 字体一旦注册就出现在游戏设置页的字体列表里，选中动作由玩家完成，
所以这里不解析 sfnt 的 `name` 表。

**`.otf` 有意不注册**：它的 `fonttype` 是 `2`(DEVICE)，游戏列表只收 `4`(TRUETYPE)，
注册成功也不会出现在列表里（见 §11）。让 `.otf` 注册成功却不显示，只会误导用户。

玩家把自己的字体丢进 `cnpatch/font/`、重启游戏，就能在「系统设定 → 字体选择」里看到并选用。
族名中英文都可以：实测纯 ASCII 族名与缺 `0x804` 记录的中文族名都能被枚举、选中并正常渲染（见 §11）。

### 10.2 默认字体：改引擎里的默认串，而不是每次启动判断

引擎没有「默认配置文件」：`FONT` 项在 `cmvs.cfg` 里**本来不存在**（实测新装 `cmvs.cfg` 无
`[MESSAGE_INFO]`）。默认字体名是写死在 exe 里的几处数据，`sub_41D490` 里就是：

```asm
push offset byte_4D0BF8      ; lpDefault = "ＭＳ ゴシック"（SJIS 全角）
push offset aFont            ; "FONT"
push offset aMessageInfo     ; "MESSAGE_INFO"
call GetPrivateProfileStringA
```

补丁在 `DllMain` 里把这 5 处默认串**就地**改成 GBK「思源黑体」（每处先逐字节校验原值，不匹配就一个字不动）：

| 地址 | 内容 | 作用 |
|---|---|---|
| `0x4D0BF8` | SJIS 全角 `ＭＳ ゴシック`（14 B + NUL） | **真正的 `lpDefault`**，默认项就从这里来 |
| `0x4E9298` | 同上 | `sub_419BF0` 把它 `lstrcpyA` 进字体对象（`sub_461840` 用） |
| `0x4E9670` | 同上 | FONT 设置全局的初值；`sub_461840` 某个分支会把 ASCII `"MS Gothic"` 覆盖进来 |
| `0x4D1AD4` | ASCII `"MS Gothic"` | 上面那个分支用的字面量（xref 仅 `0x461898`） |
| `0x4E97B0` | SJIS 全角 | 字体列表回调 `sub_491E90` 比对「当前选中项」用的副本 |

日志：`font-default patched 5/5 -> 思源黑体 (...)`。

**语义（实测两轮）**：

| `cmvs.cfg` 状态 | `CreateFontA` 的 face | 说明 |
|---|---|---|
| 无 `FONT` 项（新装） | `思源黑体` | 默认串生效 |
| `FONT=MS Gothic`（玩家自己选的） | `MS Gothic` | **原样透传，不劫持** |

所以既不需要读 `GetPrivateProfileStringA` 的钩子，也没有禁用任何字体：玩家选了什么、
配置里存了什么，引擎用的就是什么（早期版本用读钩子「强制换名」，会把这个选择挡掉，已删除）。

### 10.3 字体本身的构造

`script/build_font.py`：
- 源字体：思源黑体 CN Medium / Bold（OFL，可再分发），按 84 份中文脚本实际用字子集化 → 3393 字；
- **族名 `思源黑体`（GBK）**，`name` 表 `0x409` 与 `0x804` 同串；
- **轮廓转 glyf**：`Cu2QuPen` + `TTGlyphPen`，`sfntVersion` 改成 `\0\1\0\0`，补 `maxp`/`post`；
- `OS/2.ulCodePageRange1` 声明 GB2312(936)；
- 两个 face：Medium(500) + Bold(700)，对应引擎请求的 400 / 600（`cWeight @ 0x4D1AC0 = {0,200,400,600,600}`）；
- 产物 `patch/cnpatch/font/SourceHanSansCN-{Medium,Bold}.ttf`（文件名沿用官方命名，后缀跟内容走 = `.ttf`）。
- 运行期 `AddFontResourceExW(..., FR_PRIVATE, 0)`；DllMain 与工作线程各注册一次，避免时序问题。

## 11. 随包字体的两条硬性要求（实测闭环）

要让"游戏内字体列表能看到、设置界面显示的名字与实际渲染一致"，字体必须同时满足：

| 要求 | 证据 / 原因 |
|---|---|
| **必须是 TrueType(glyf) 轮廓** | OTF(CFF) 字体在 `EnumFontFamiliesExA` 回调里 `fonttype=2`(DEVICE)；游戏列表只收 `fonttype=4`(TRUETYPE)，所以 OTF 永远不出现在列表里（`script/build_font.py` 用 `Cu2QuPen` + `TTGlyphPen` 转 glyf，并把 `sfntVersion` 改成 `\0\1\0\0`） |
| **配置显示名 = `CreateFontA` 用名** | 两者写成不同的串（曾出现"配置返回中文名、`CreateFontA` 换 ASCII 名"）时，GDI 匹配不到字族会退回西文字体，整个文本对象都变豆腐块 |

族名用中文只是为了让下拉框里好看，不是硬性要求：实测纯 ASCII 族名（`AOTEST`）与缺 `0x804` 记录的中文族名都能正常枚举、选中、渲染。引擎自己绘制字体名的路径确实会在双字节模式下把 ASCII 名两两配对（`AAAA` → `侬傲…`），但那与「字体能不能被选中」是两条路径，不要混淆（详见 `lessons-learned.md` §4）。

字重方面：引擎只请求 `400`（正文）与 `600`（粗体），见 `.rdata` `0x4D1AC0` 的 `cWeight = {0,200,400,600,600}`。所以随包字体出 **Medium(500) + Bold(700)** 两个 face，让 GDI 分别就近命中，避免合成加粗发毛。
