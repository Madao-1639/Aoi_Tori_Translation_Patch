# Aoi Tori 简中补丁

## 项目目的

为 Steam 版《Aoi Tori》（appid 2741260，CMVS 引擎 `cmvs32[ver.3.80]` / `ps3[ver.1.00]`）产出**简体中文补丁**，
要求：不替换 exe、不改动原生文件、Steam 直接启动（保留时长统计），安装=复制文件。

文本来源：【天之圣杯汉化组】青鸟汉化补丁（`Longinus.dat`：85 份完整中文 PS2A 脚本 + 52 张界面图）。

## 技术结论速查

见 `doc/engine-mechanics.md`（结论唯一来源，含 IDA 地址与复跑命令）。要点：

| 项 | 结论 |
|---|---|
| 外挂覆盖 | `data/update/`（脚本槽 0）与 `data/image/update/`（图片槽 0）优先级最高，原生文件不动 |
| 中文字形 | charset 由 `initial.cfg` 的 `FONT_SHIFT_JIS` 决定（`0x80`/`0x00`），exe 无 `AddFontResource`，必须改代码 |
| 代码补丁 | 9 处 `cmp X,0x9F` 立即数 `0x9F→0xFE`；`CreateFontA` 的 charset `0x80→0x86` |
| 生效通路 | 代理 `mog3x32.dll`（音频库，19 导出，静态导入）→ DllMain 内存补丁 + `AddFontResourceExW(FR_PRIVATE)` |
| 脚本容器 | PS2A；`compressed_size=0` 时引擎跳过 LZSS，补丁脚本可不压缩 |

## 工程约定

### 1. 文档同步
任何结论性修改必须落到 `doc/`：
- 引擎行为、查找顺序、补丁地址、字体渲染 → `doc/engine-mechanics.md`
- 踩坑与返工教训 → `doc/lessons-learned.md`

文档只写**当前成立**的最新结论，每条带验证依据（可复跑命令或文件指针）。

### 2. 去隐私化
本机路径不进跟踪文件：`AGENTS.md`、`doc/`、脚本示例里用 `<游戏目录>` / `<汉化补丁>` 占位符；
实际取值写在不入库的 `AGENTS.local.md`（`*.local.*` 已 gitignore）。

### 3. 不入库内容
`resource/`、`patch/data/`、`patch/*.dll`、`patch/font/*.ttf`、`releases/`、`tmp/`
——均为游戏或汉化补丁内容，可由 `script/` 重建。

### 4. 构建环境
- Python：`EDA` 环境（numpy/pandas/fonttools/Pillow 已装；涉及第三方库的脚本必须先 `mamba activate EDA` 或 `mamba run -n EDA`）
- DLL：本机 m2w64（`x86_64-w64-mingw32-gcc -m32` + `ld -m i386pe`），不新增安装

### 5. 验证要求
任何补丁改动都要能回答：改了什么字节、原始字节是什么、失败时是否保留原状。
内存补丁一律「先校验期望字节再写」，不允许盲写。
