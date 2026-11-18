# Aoi Tori 简中补丁（Steam 版）

## 项目目的

为 Steam 版《Aoi Tori》（appid 2741260，CMVS 引擎 `cmvs32[ver.3.80]` / `ps3[ver.1.00]`）产出**简体中文补丁**。

硬性要求：**不替换 exe**、Steam 直接启动（时长统计/云存档/成就正常）、安装 = 复制文件、卸载 = Steam 验证完整性或删文件。

对原生文件的唯一改动是 `mog3x32.dll`：纯外挂方案走不通（见「技术背景 · 注入点」），必须在原生 DLL 上追加代码。其余原生文件（`cmvs32.exe`、`data/code/*.ps3`、`data/image/**`）一律只读。

素材来源：【天之圣杯汉化组】青鸟汉化补丁 `Longinus.dat`（85 份完整中文 PS2A 脚本 + 52 张界面图，整脚本替换）。

**分阶段目标**：
1. 工程骨架与约定
2. 机制判定：外挂覆盖槽位、双字节判定、charset 通路、注入点（见 `doc/engine-mechanics.md`）
3. 脚本交付：PS2A 编解码 + 84 份中文脚本（`start.ps3` 天然读不到，已排除）
4. 代码补丁与代理 DLL（9 处引导字节 + `CreateFontA` charset + 版本闸门）
5. 字体与界面：思源黑体子集化、PB3B 界面图、标题栏
6. 验收证据与文档收尾

**不在范围**：`intcode.ps3`（汉化组同样未翻；纯 ASCII 内部调试标签）、翻译质量把关（沿用汉化组文本，只做编码与容器层面的对接）。

## 技术背景（结论速查）

细节见 `doc/`：`engine-mechanics.md`（槽位/查找顺序/地址/字体/PB3B 布局）、`lessons-learned.md`（踩坑与规则）。

| 项 | 结论 |
|---|---|
| 引擎 | CMVS `cmvs32[ver.3.80]` / `ps3[ver.1.00]`；exe SHA-256 `3a2ec3af…53727e`，image base `0x400000`，SizeOfImage `0x151000` |
| 外挂覆盖 | 原生 `start.ps3` 把 `data\update\`（脚本槽 0）与 `data\image\update\`（图片槽 0）挂在最高优先级，查找从槽 0 起、**首个命中即胜** ⇒ 只增文件即可覆盖，原生文件不动 |
| `start.ps3` 自举 | 它自己负责挂 `data/update`，所以它只能来自 `data/code/`；放进 `data/update/` 是**死文件**（交付清单里排除） |
| 脚本容器 | PS2A：`"PS2A" + 48 B 头 + 字节变换 + 可选 LZSS`；`plain = ror8(((enc-124)&0xFF)^xor, shift)`，`compressed_size = 0` 时引擎**完全跳过** LZSS ⇒ 补丁脚本可以不压缩（`tool/ps3.py`，85/85 往返一致） |
| 图片容器 | PB3B **type 1**：头部 8..52 自加密，`flags 区`（每通道 12 B 元数据 + 分块标记 + 常量块 + LZSS 位流）+ `data 区`（字面像素）；字面像素是**分块优先序**。引擎不校验 checksum，可全走字面（`tool/pb3.py`，往返逐像素一致） |
| 中文字形 | 转码只看 `LOGFONT.charset`（`0x80`=SJIS / `0x86`=GB2312）；exe 里**没有** `MultiByteToWideChar`、`AddFontResource` ⇒ 必须改 `CreateFontA` 的 charset + 自己注册字体 |
| 双字节判定 | 内联 9 份 `cmp X, 0x9F`（`0x4182F9 0x419D39 0x41A04B 0x41A089 0x41A0D6 0x41A146 0x41A1B9 0x41A2CB 0x41A3A9`，改**立即数字节** `0x9F→0xFE`，不按指令地址改）；另有双字节模式标志 `dword_4EF580`，由 `sub_486C40` 切换 |
| 注入点 | **改 `mog3x32.dll`**：引擎自带 OGG 音频库、19 个导出、被静态导入 ⇒ 无条件生效、Wine 也加载。`dinput8`/`DSOUND` 条件生效，`version`/`winmm` 在 KnownDLLs，`dxgi`/`d3d11` 在 Wine 走 builtin。**在原生 DLL 上追加代码**，不替换、不转发 |
| 版本闸门 | 9 处引导字节必须**全部**命中，任一不中则整体不应用（日志 `DISABLED`），避免游戏更新后一半中文一半乱码 |
| 字体 | 随包思源黑体 CN（Medium 500 + Bold 700，子集 3393 字，OFL）`AddFontResourceExW(FR_PRIVATE)` 注册。三个硬性要求：**TrueType(glyf)** 轮廓（OTF/CFF 的 `fonttype=2` 不进游戏列表）、**GBK 中文族名**（双字节模式会把 ASCII 名两两配对）、**配置显示名 = `CreateFontA` 用名**（不一致就整屏豆腐块）。字重表 `cWeight @ 0x4D1AC0 = {0,200,400,600,600}` |
| 配置 | **不接管** `cmvs.cfg FONT=`（引擎退出会写回覆盖）⇒ 走 `GetPrivateProfileStringA` 钩子只对 `FONT` 返回我们的名字 |
| 标题栏 | 标题串固定在 `data/code/start.ps3`：`Aoi Tori[ver.1.03] R18` + SJIS 全角空格 `81 40` + `- 章节名 -`；`81 40` 在 GBK 下解成「号」⇒ `SetWindowTextA` 钩子换前缀 + 等长替换 `A1 A1` |
| 素材形态 | `Longinus.dat` 里脚本是**整份 PS2A**（不是填格），图片是**裸 BGRA**（无容器，名字靠脚本里的 `*.pb3` 字符串反查） |

## 工程约定

### 1. 文档同步要求

任何结论性修改都必须落到 `doc/`，不要只留在对话里：

| 改动 | 必须更新 |
|---|---|
| 引擎行为、查找顺序、补丁地址、字体链路、PB3B 布局 | `doc/engine-mechanics.md` |
| 踩坑与返工教训（含「用户反驳 AI」的案例） | `doc/lessons-learned.md` |
| 安装/卸载、交付形态、完成进度 | `README.md` |
| 阶段目标、工程约定、环境 | `AGENTS.md` |

文档只写**当前成立的最新结论**，每条结论必须带验证依据（可复跑的命令或文件指针）。

### 2. 去隐私化

本机路径不进跟踪文件：`AGENTS.md`、`doc/`、`README.md` 与脚本示例里一律用 `<游戏目录>` / `<汉化补丁>` / `<m2w64>` 占位符；实际取值只记在**不入库**的 `AGENTS.local.md`（`*.local.*` 已 gitignore）。

### 3. 目录与脚本组织

```
tool/       长期复用的库（ps3 / pb3 / longinus）
script/     项目流程脚本（提取、字体、界面图、组装），从项目根执行
doc/        知识文档
resource/   可复用的数据产物（中文脚本、裸图、字体；不入库，可由 script/ 重建）
dll/        代理 DLL 与补丁 DLL 的 C 源码 + `build.sh`
patch/      构建输出（安装即复制其内容；整目录不入库）
tmp/        临时文件，任务结束即清理（不入库）
releases/   发布压缩包（不入库）
```

- 所有脚本用相对项目根的路径，从项目根执行。
- 一次性脚本、调试输出、解包出来的原始资源一律放 `tmp/`，任务结束删除；需要保留的中间结果移入 `resource/`。
- 补丁自身文件**不放进 `data/`**：只有引擎会读的（`.ps3`、`.pb3`）才进 `data/`，其余（DLL、字体、日志）进 `cnpatch/`。

### 4. 脚本开发规范

- **幂等**：重复运行不得产生不同结果。
- **语料即源**：不建旁路覆盖表；要改内容就改 `resource/patch/`。
- **回读校验**：任何写操作（生成 `.ps3`、编码 `.pb3`、子集化字体）之后必须重新解析产物并断言与预期一致（PS2A 往返一致、PB3B 逐像素一致、字体 `EnumFontFamiliesExA` 可见）。
- **零改动等价**：编解码器必须支持「原样往返」——85/85 脚本、5 份游戏原图都做过逐字节比较。
- **报错优先**：名字对不上、尺寸不符、magic 不对，一律打印后跳过或抛异常，禁止静默写出可疑文件。

### 5. 编码约定

- 中文文本一律 **GBK**：脚本 `text` 段、字体族名、`SetWindowTextA` 写出的标题。写 C 代码时避免 `\uXXXX` 转义，用 `(char)0xNN` 字节数组并加注释。
- 日志 `cnpatch.log` 是 GBK，键值用 `\t` 分隔（`reset_line` / `put` / `flush_line`），控制台看是乱码属正常。
- 运行时**不要**用 `printf` 到 stdout（Steam 直启没有控制台），一律写日志文件。
- 二进制补丁脚本一律 `128` 列内、注释写「期望字节 + 原始字节 + 为什么」。

### 6. Commit message

用 **Conventional Commits**，中文书写。scope 用小写模块名（`ps3` / `pb3` / `font` / `dll` / `patch` / `doc`），多个用逗号分隔。

```
<type>(<scope>): <简述>

- <高层结论或取舍，一行一条>
```

- `type`：`feat` / `fix` / `refactor` / `docs` / `chore` / `test`。
- **代码标识符、路径、命令、常量一律用反引号包裹**：`tool/pb3.py`、`--image-base 0x6A000000`、`0x9F→0xFE`。
- `body` 只写结论与取舍，不复述 diff；判据与复验步骤由 `doc/` 承载。
- **未经明确许可不要 commit**（本项目的工作区改动默认处于未提交状态，用户会自己看过再决定）。

### 7. 安全与备份

- **原生文件只读**：`cmvs32.exe`、`data/code/*.ps3`、`data/image/**` 一律不写、不替换。
- **唯一例外是 `mog3x32.dll`**：外挂式方案做不到注入（引擎认中文需要改 exe 内的编码通路，而改 exe 就失去 Steam 直启），只能改这个被静态导入的 DLL。改法是**在原生 DLL 上追加**，原生 19 个导出与实现原地保留；不采用「替换 + 转发」造 `.orig` 的方案。
- 恢复原生状态 = Steam 验证游戏完整性（本来就会把 `mog3x32.dll` 还原）。
- 不动 `cmvs.cfg`（引擎退出会写回，改了也会被覆盖）。
- 所有构建产物先写到项目目录（`patch/`），再按安装步骤复制进游戏目录；绝不直接在游戏目录里构建。
- 覆盖任何已产出补丁前先备份（`*.bak`）并在 `doc/` 记录原因。
- 不把游戏/汉化组素材提交进仓库：`resource/`、`patch/data/`、`patch/*.dll`、`patch/cnpatch/*.ttf`、`releases/`、`tmp/` 均在 `.gitignore`。

### 8. 测试与验收

每个阶段结束前：跑该阶段校验脚本 → 人工实机确认 → 更新文档 → 清理 `tmp/`。

- **离线闸**（可自动化）：PS2A 往返、PB3B 往返、字体枚举、清单与文件数一致、`.ps3` 数量与源一一对应。
- **游戏内确认**（人工）：剧情对白、菜单/设置/存读档贴图、标题栏、存档退出再进。
- 内存补丁一律「先校验期望字节再写」，不允许盲写；失败必须保持原状（版本闸门即为此）。

## 环境要求

- **Python**：3.12，需 `fonttools`、`Pillow`、`numpy`、`scipy`。用 conda/mamba 环境管理，执行方式 `mamba run -n <环境名> python script/xxx.py`。
- **C 工具链**：32 位 MinGW-w64（`x86_64-w64-mingw32-gcc -m32` + `ld -m i386pe`）。
  - 补丁本体：`M2W64_BIN="<m2w64>/bin" bash dll/build.sh`
  - `mog3x32.dll`：`M2W64_BIN="<m2w64>/bin" bash dll/build_loader.sh <原生 mog3x32.dll>`（在原生 DLL 上追加引导节）
  - 产物均写进 `patch/`。
- **静态分析**：IDA Pro（可选，仅逆向 exe 时需要）。
- **外部输入**：`<游戏目录>`（Steam 版）、`<汉化补丁>/Longinus.dat`。

本机具体取值（环境名、绝对路径、依赖版本等）记在**不入库**的 `AGENTS.local.md`。

## 参考资料

- [cmvs-rs](https://github.com/uyjulian/cmvs-rs)：CMVS 容器与 PB3B 解码的参考实现（本项目 PB3B 布局以它的 `pb3.rs` 为准）。
- [GARbro](https://github.com/morkt/GARbro)：CPZ / PB3B 格式对照。
- [9nine_EP1_Retranslation_Patch](https://github.com/Madao-1639/9nine_EP1_Retranslation_Patch)：本项目的工程架构来源（目录组织、脚本规范、文档分工、去隐私化）。
