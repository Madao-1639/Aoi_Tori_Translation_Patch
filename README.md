# 青鸟 汉化补丁

本补丁是对 [daintf 适配的【天之圣杯汉化组】青鸟汉化补丁](https://www.kfpromax.com/read.php?tid=1034230&sf=1d4)（下称「原补丁」）的重构：安装更简单，由 Steam 直接启动，时长统计与云存档正常；文本和 UI 素材完全复用。

> 本仓库只包含实现所需的分析与工具，不含游戏本体与汉化组素材文件；补丁仅供已购买正版的用户使用。

![Banner](https://shared.steamstatic.com/store_item_assets/steam/apps/2741260/library_hero.jpg)

---

## 为什么要重构

原补丁的问题在于它替换了游戏的启动程序：只有启动它带来的 `青鸟_CHS.exe` 才能进中文版，无法通过 Steam 启动；可以通过改名 + 改 ini 配置项解决，但操作比较繁琐。

| | 原补丁 | 本补丁 |
|---|---|---|
| 文本与 UI 素材 | 汉化组原版 | 相同，未改动 |
| 启动方式 | 自带的 exe，不经过 Steam | Steam 直接启动，时长/云存档/成就正常 |
| 安装 | 放文件 + 改名 + 改 ini | 拖进游戏目录即可 |
| 字体 | 默认用 MS Gothic，有的字粗有的字细 | 内置思源黑体，统一字体粗细；[添加你自己的字体](#换成你自己的字体) |

## 安装 / 卸载 / 更新

**前置**：已按官方要求装好 **AoiToriR18Patchv1.04**（本补丁的中文脚本对应 18+ 内容版本）。

**安装**：

1. 从 [Releases](../../releases) 下载发布压缩包并解压；
2. 打开游戏安装目录（Steam 库 → 右键游戏 →【管理】→【浏览本地文件】）；
3. 补丁会覆盖官方的 `mog3x32.dll`（唯一被改动的原生文件）。想留个后手可先把它复制一份存到别处；
4. **把解压出来的所有文件和文件夹整体拖进游戏目录**，覆盖同名文件；
5. 从 Steam 正常启动游戏即可，无需在游戏内做任何设置。

**更新**：下载新版压缩包解压，覆盖 `mog3x32.dll`、`cnpatch/` 与 `data/` 下的同名文件即可。

**卸载**：删掉 `cnpatch/` 和 `data/update/`、`data/image/update/` 里本补丁新增的文件，然后在 Steam 里验证游戏完整性，`mog3x32.dll` 会被还原成官方版本。

补丁不替换 `cmvs32.exe`，不改动 `data/code`、`data/image` 里的原生文件。唯一改动的官方文件是 `mog3x32.dll`——中文必须改引擎的编码通路，而它全在 exe 里，不改 exe 就只能在这个被静态导入的 DLL 上动手。

## 字体

### 为什么要内置字体

引擎的默认字体是 `MS Gothic`，只有日文字形。简体中文缺字时引擎会临时回落到系统默认字体，同一句话里「有字形」和「缺字形」的字符就落到不同字体上，笔画粗细和字面大小都不一样。

本补丁内置思源黑体 CN 作为默认字体，装上就是正常字形，也允许玩家添加字体。

### 添加你自己的字体

把你喜欢的字体文件放进 `<游戏目录>/cnpatch/font/`，重启游戏，进【系统设定】→【其他设定】→【字体选择】即可选到。

- 目录下所有 `.ttf` 都会被自动扫描注册，可以和内置字体共存。`.otf` 不注册——它进不了游戏列表（见下）。
- 注册是**私有**的，只对该游戏可见，**不会安装到你的系统字体目录**，卸载补丁后系统里不会留下任何东西。

### 字体选择的要求

**选 TrueType（`.ttf`）字体**。OTF/CFF 字体不被游戏接受，无法出现在字体列表中。

内置字体就是从思源黑体子集化来的：转成 TrueType 轮廓（如果是 `.otf`）、改中文族名、裁到本作用字集合，然后随补丁一起发布。构建脚本见 `script/build_font.py`。

---

## 实现原理

### 文件的加载

**素材不用改代码。** 引擎启动时会把 `data\update\` 与 `data\image\update\` 挂在查找路径的最高优先级，首个命中即生效。所以把汉化组的同名文件放进这两个目录就能覆盖脚本与贴图，原生文件一个字节都不用动。

**编码必须改代码。** 引擎不支持简体中文：它只认 Shift-JIS 的双字节范围，字体的字符集也只有日文与西文两档，且这些判断硬编码在 `cmvs32.exe` 里。改 exe 会失去 Steam 直启，所以只能在游戏运行时改进程内存。

### 注入点的选择

运行时改内存需要先进入游戏进程。候选的 DLL 要同时满足两点：被 exe 静态导入（加载时机确定），以及 Windows 与 Wine 都优先从 exe 目录加载。

逐个排除后只剩 `mog3x32.dll`（引擎自带的 OGG 音频库）：`dinput8`/`DSOUND` 有加载条件，`version`/`winmm`/`gdi32` 在 `KnownDLLs` 里劫持无效，`dxgi`/`d3d11` 在 Wine 下走 builtin。

做法是在原生 `mog3x32.dll` 上追加一个代码节，并把入口点指过去。原生代码、导出表、19 个音频函数全部原地保留，所以音频行为不变——不是转发，是原生实现根本没被碰过。

### 两个 DLL 的分工

- **`mog3x32.dll`**（追加了引导节）只负责一件事：等 GDI 就绪后加载补丁本体。它不参与中文相关的任何逻辑。
- **`cnpatch.dll`** 做全部实际工作：校验版本、改双字节判定、钩 GDI 取字形、注册随包字体、处理标题栏。

为什么不在 `DllMain` 里直接加载补丁：此时进程还持有加载器锁，`LoadLibrary` 会被拒绝。所以引导节开一个线程延后加载。

---

## 已知情况

| 项 | 说明 |
|---|---|
| 前置要求 | 需先装官方 **AoiToriR18Patchv1.04**（中文脚本对应 18+ 内容版本） |
| 汉化范围 | 剧情、选项、界面、标题栏、标题菜单；字体可换 |
| 未汉化 | `intcode.ps3`——引擎内部调试标签，汉化组同样未翻，没有可读文本 |
| 汉化组素材的 46 处遗漏已补 | 原文照抄日文、说话人/章节标题未译，以日文原文为准补成中文；术语与引号沿用汉化组约定 |
| 有意不译 | 语气词/喘声（`っ`）、译注引用的日文原文、`{表情/かお}` 排版标注 |
| 少量 UI 素材沿用原版 | 汉化组素材本身缺 13 张图，另有 1 张分辨率不符 |
| 系统要求 | Windows；Steam Deck / Proton 可用（代理注入即为此设计）。32 位游戏 |

## 目录结构

```
doc/   技术文档（引擎机制、踩坑教训）
tool/  通用库（PS2A 编解码 / PB3B 编解码 / 文本就地编辑 / Longinus 素材解析）
script/ 流程脚本（提取 / 补译 / 字体 / UI 素材 / 组装），从项目根执行
dll/   代理 DLL 与补丁 DLL 的 C 源码 + 构建脚本
patch/ 构建输出（安装即复制其内容，整目录不入库）
resource/ 提取产物（语料、图片、字体，可由 script/ 重建，不入库）
tmp/   临时文件，任务结束即清理（不入库）
releases/ 发布压缩包
```

## 发布

全量包 = `patch/` 的全部内容，直接 zip，不需要脚本：

```bash
python script/build_patch.py --version <版本>
cd patch && zip -r -9 ../releases/Aoi_Tori_CN_Patch_v<版本>.zip . -x 'manifest.json' -x 'meta/*'
```

最小升级包才需要脚本，因为要算差异：

```bash
python script/build_patch.py --version <版本>
python script/build_update.py --from <上一版>
```

升级包的正确性定义：把它铺到上一版的文件上，结果必须与当前 `patch/` 逐字节一致。
`build_update.py` 生成后逐条目校验 SHA-256。

基线用 `patch/meta/v<版本>/manifest.json`，**不用 `releases/` 里的旧 zip**：那个目录
不入库、随时会被清理。也不要拿游戏目录当基线——里面有官方 R18 补丁的图，
会把它们误报成「新版不再提供」。

归档基线时 `version` 要写那一版自己的号、`sha256` 要取自发布包（字体可能在发布后又重新子集化过），
所以从上一版的 zip 重建而不是拷工作区那份：

```bash
python - "1.0.1" <<'PY'
import hashlib, json, sys, zipfile
prev = sys.argv[1]
path = "patch/meta/v%s/manifest.json" % prev
data = json.load(open(path, encoding="utf-8"))
with zipfile.ZipFile("releases/Aoi_Tori_CN_Patch_v%s.zip" % prev) as z:
    blob = {n: z.read(n) for n in z.namelist() if not n.endswith(".txt")}
files = [{"path": e["path"], "bytes": len(blob[e["path"]]),
          "sha256": hashlib.sha256(blob[e["path"]]).hexdigest()} for e in data["files"]]
with open(path, "w", encoding="utf-8") as f:
    f.write(json.dumps(dict(data, version=prev, files=files),
                       ensure_ascii=False, indent=2, sort_keys=True) + "\n")
PY
```

## 完成进度

- [x] 引擎内部实现：不替换 exe，Steam 直启（覆盖槽位 + 运行期内存补丁）
- [x] 中文编码通路：双字节判定、字体 charset、**标点禁则**（`、。」』` 不落行首）
- [x] 剧情脚本 84 份、UI 贴图 38 张，全部实机生效
- [x] 补译汉化组遗漏的 46 处（未译整句、说话人名、章节标题）
- [x] 标题栏汉化，顺手修掉原版的「R18号」乱码
- [x] 随包字体：默认即正确字形与字重，且允许玩家自带字体
- [x] 安装：整包拖入游戏目录，无需改名或改配置
- [ ] 全流程抽验：逐章进度、存读档/回想/鉴赏、各结局分支

## 反馈

遇到问题请提交 [Issue](../../issues)，附上：

- 补丁版本
- 出现问题的具体场景（大致进度；最好附上存档文件）
- 游戏弹出的错误日志（如有）
- 问题的具体表现

## 致谢

- 汉化素材：【天之圣杯汉化组】
- [cmvs-rs](https://github.com/Shizuku-in/cmvs-rs)：CMVS 格式与 PB3B 解码的参考实现
- [GARbro](https://github.com/morkt/GARbro)：CPZ / PB3B 格式对照
