// cnpatch.dll —— Aoi Tori 简中补丁（改动全部只存在于进程内存，不修改任何原生文件内容）
//
// 由代理 mog3x32.dll 在 DllMain 里按需加载，游戏是 32 位进程。
//
// 四件事：
//   [1] 双字节判定：引擎把「Shift-JIS 首字节」判定内联了 9 份，形状固定为
//         cmp X,0x81 / jb / cmp X,0x9F / jbe      （另一支 lea t,[X+0x20]; cmp t,0x1C 判 0xE0-0xFC）
//       GBK 首字节可到 0xFE，且汉字的引导字节（C4 CA D2 B8…）落在 0xA0-0xDF——那是 SJIS
//       半角片假名区，被原判定排除，导致「一个字占几格」按半角算、正文框缩窄一半。
//       9 处立即数字节 0x9F -> 0xFE（注意 3C 9F 与 80 F? 9F 两种编码混排，改的是立即数）。
//   [2] 字体 charset：CreateFontA 的 iCharSet 只有 0x80(SJIS)/0x00 两档，来源是
//       initial.cfg 的 FONT_SHIFT_JIS。用 *IAT 钩子* 把 charset 强制成 0x86(GB2312)，
//       比逐点改 push 80h 更稳（不挑调用点，也不受编译器布局差异影响）。
//   [3] 字体：随包 TTF 用 AddFontResourceExA(FR_PRIVATE) 私有注册，只对本进程可见，
//       不写系统字体目录（免安装）；随后把字体名换成该私有字体。
//   [4] 日志：data/update/cnpatch.log，记录每个补丁点的校验结果与每次建字体的参数。
//
// 安全约定：所有字节补丁先校验原字节，不符则跳过并记日志，绝不盲写。

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long ulong;
typedef void* HMODULE;
typedef void* HANDLE;

#define FR_PRIVATE 0x10
#define DLL_PROCESS_ATTACH 1
#define GENERIC_WRITE 0x40000000u
#define OPEN_ALWAYS 4
#define FILE_ATTRIBUTE_NORMAL 0x80
#define FILE_END 2

// ───────────────────────── 最小运行时（无 CRT / 无导入库） ─────────────────────────

typedef struct {
    u16 length;
    u16 maximum_length;
    u16* buffer;
} UNICODE_STRING;

typedef struct {
    u32 length;
    u32 initialized;
    u32 ss_handle;
    u32 in_load_order;   // LIST_ENTRY.Flink
} PEB_LDR_DATA;

typedef struct {
    u8 inherited_address_space;
    u8 read_image_file_exec_options;
    u8 being_debugged;
    u8 bit_field;
    u32 mutant;
    u32 image_base_address;
    PEB_LDR_DATA* ldr;
} PEB;

#define LDR_DLL_BASE 0x14
#define LDR_BASE_DLL_NAME 0x28

static PEB* peb(void)
{
    PEB* value;
    __asm__ __volatile__("movl %%fs:0x30, %0" : "=r"(value));
    return value;
}

static int upper(int c) { return (c >= 'a' && c <= 'z') ? c - 32 : c; }

static int eq_ci(const char* a, const char* b)
{
    while (*a && *b) {
        if (upper((u8)*a) != upper((u8)*b)) return 0;
        ++a, ++b;
    }
    return *a == *b;
}

static int eq_wide_ci(const u16* a, int bytes, const char* b)
{
    int i;
    for (i = 0; i < bytes / 2; ++i) {
        if (!b[i]) return 0;
        if (a[i] < 0x80 ? upper(a[i]) != upper((u8)b[i]) : 0) return 0;
    }
    return b[bytes / 2] == 0;
}

static u32 module_base(const char* name)
{
    u32 head, entry;
    int offset;
    PEB* p = peb();
    if (!p || !p->ldr) return 0;
    head = (u32)&p->ldr->in_load_order;
    for (entry = *(u32*)head; entry && entry != head; entry = *(u32*)entry) {
        /* BaseDllName 相对 DllBase 恒为 +0x14；不同 Windows 版本里它在 0x24/0x28/0x2C，
           逐个试，命中即用（DllBase = 表项 + 偏移 - 0x14）。 */
        for (offset = 0x24; offset <= 0x2C; offset += 4) {
            UNICODE_STRING* base_name = (UNICODE_STRING*)(entry + offset);
            if (base_name->length < 2 || base_name->length > 128 || !base_name->buffer) continue;
            if (eq_wide_ci(base_name->buffer, base_name->length, name))
                return *(u32*)(entry + offset - 0x14);
        }
    }
    return 0;
}

static u32 export_lookup(u32 module, const char* name)
{
    u8* image;
    u32 pe, exports, count, names, functions, ordinals, i;
    if (!module) return 0;
    image = (u8*)module;
    pe = *(u32*)(image + 0x3C);
    if (*(u32*)(image + pe) != 0x00004550) return 0;
    exports = *(u32*)(image + pe + 24 + 96);   /* DataDirectory[0] = 导出表 RVA */
    if (!exports) return 0;
    count = *(u32*)(image + exports + 24);     /* NumberOfNames */
    names = *(u32*)(image + exports + 32);
    functions = *(u32*)(image + exports + 28);
    ordinals = *(u32*)(image + exports + 36);
    if (!names || !functions || !count || count > 65536) return 0;
    for (i = 0; i < count; ++i) {
        u32 entry_name = *(u32*)(image + names + 4 * i);
        if (entry_name && eq_ci((const char*)(image + entry_name), name)) {
            u16 ordinal = *(u16*)(image + ordinals + 2 * i);
            u32 function = *(u32*)(image + functions + 4 * ordinal);
            /* 导出表里若落在导出段内则是 forwarder，跳过 */
            if (function >= exports && function < (*(u32*)(image + pe + 24 + 96 + 4))) return 0;
            return module + function;
        }
    }
    return 0;
}

typedef HMODULE(__stdcall* FN_LoadLibraryA)(const char*);
typedef void*(__stdcall* FN_GetProcAddress)(HMODULE, const char*);
typedef int(__stdcall* FN_VirtualProtect)(void*, ulong, ulong, ulong*);
typedef int(__stdcall* FN_AddFontResourceExA)(const char*, ulong, void*);
typedef HANDLE(__stdcall* FN_CreateFileA)(const char*, u32, u32, void*, u32, u32, HANDLE);
typedef int(__stdcall* FN_WriteFile)(HANDLE, const void*, u32, u32*, void*);
typedef u32(__stdcall* FN_GetModuleFileNameA)(HMODULE, char*, u32);
typedef int(__stdcall* FN_CloseHandle)(HANDLE);
typedef u32(__stdcall* FN_SetFilePointer)(HANDLE, long, long*, u32);
typedef void(__stdcall* FN_ExitProcess)(u32);
typedef int(__stdcall* FN_GetEnvironmentVariableA)(const char*, char*, u32);
typedef void*(__stdcall* FN_GetDC)(void*);
typedef int(__stdcall* FN_ReleaseDC)(void*, void*);
typedef struct {
    u32 attr;
    u32 time[6];
    u32 size_high;
    u32 size_low;
    u32 reserved[2];
    char name[260];
    char alt[14];
} FIND_DATA_A;                       /* 与 Win32 WIN32_FIND_DATAA 同布局（320 字节） */

typedef void*(__stdcall* FN_FindFirstFileA)(const char*, FIND_DATA_A*);
typedef int(__stdcall* FN_FindNextFileA)(void*, FIND_DATA_A*);
typedef int(__stdcall* FN_FindClose)(void*);
static FN_FindFirstFileA pFindFirstFileA;
static FN_FindNextFileA pFindNextFileA;
static FN_FindClose pFindClose;

static FN_GetDC pGetDC;
static FN_ReleaseDC pReleaseDC;

static FN_LoadLibraryA pLoadLibraryA;
static FN_GetProcAddress pGetProcAddress;
static FN_VirtualProtect pVirtualProtect;
static FN_AddFontResourceExA pAddFontResourceExA;
static FN_CreateFileA pCreateFileA;
static FN_WriteFile pWriteFile;
static FN_GetModuleFileNameA pGetModuleFileNameA;
static FN_CloseHandle pCloseHandle;
static FN_SetFilePointer pSetFilePointer;

static HMODULE g_self;

// ───────────────────────── 日志（仅调试构建） ─────────────────────────
//
// 日志是开发期诊断手段，不是功能：发布版不写任何文件（不改动用户磁盘、无残留）。
// 需要日志时：`DEBUG=1 bash dll/build.sh`。

#ifdef CNPATCH_DEBUG

static char g_line[512];
static int g_at;

static void reset_line(const char* tag)
{
    g_at = 0;
    while (*tag && g_at < 480) g_line[g_at++] = *tag++;
    g_line[g_at++] = '\t';
}

static void put(const char* text)
{
    while (*text && g_at < 500) g_line[g_at++] = *text++;
}

static void put_hex(u32 value, int digits)
{
    static const char hex[] = "0123456789abcdef";
    int i;
    for (i = digits - 1; i >= 0 && g_at < 500; --i) g_line[g_at++] = hex[(value >> (4 * i)) & 0xF];
}

static void put_dec(int value)
{
    char digits[12];
    int n = 0, i;
    if (value < 0) { g_line[g_at++] = '-'; value = -value; }
    if (value == 0) digits[n++] = '0';
    while (value > 0) { digits[n++] = (char)('0' + value % 10); value /= 10; }
    for (i = n - 1; i >= 0; --i) if (g_at < 500) g_line[g_at++] = digits[i];
}

static void flush_line(void)
{
    static char path[512];
    HANDLE file;
    u32 written;
    char* tail;
    char* scan;
    int n = 0;

    if (!pCreateFileA || !pWriteFile || !pGetModuleFileNameA) return;
    if (!pGetModuleFileNameA(g_self, path, 460)) return;
    tail = path;
    for (scan = path; *scan; ++scan) if (*scan == 92) tail = scan;
    *tail = 0;
    while (path[n] && n < 440) ++n;
    {
        const char* suffix = "\\cnpatch.log";
        int i = 0;
        while (suffix[i] && n < 500) path[n++] = suffix[i++];
    }
    path[n] = 0;

    file = pCreateFileA(path, GENERIC_WRITE, 3, 0, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, 0);
    if (file == (HANDLE)-1) return;
    if (pSetFilePointer) pSetFilePointer(file, 0, 0, FILE_END);
    g_line[g_at++] = '\r';
    g_line[g_at++] = '\n';
    pWriteFile(file, g_line, (u32)g_at, &written, 0);
    pCloseHandle(file);
}

#else   /* 发布版：全部日志调用展开为空，不引用任何文件 API */

#define reset_line(tag)     ((void)0)
#define put(text)           ((void)0)
#define put_hex(value, d)   ((void)0)
#define put_dec(value)      ((void)0)
#define flush_line()        ((void)0)

#endif

// ───────────────────────── [1] 双字节判定 9 处 ─────────────────────────
static int g_enabled = 1;   /* 版本闸门：9 处校验必须全中 */
static u32 g_enum_count;

typedef struct {
    u32 rva;      // 立即数字节的 RVA（相对 exe 基址）
    u8 expected;
    u8 replacement;
} byte_patch;

// 原始 VA: 0x4182F9 0x419D39 0x41A04B 0x41A089 0x41A0D6 0x41A146 0x41A1B9 0x41A2CB 0x41A3A9
static const byte_patch k_lead_byte_patches[] = {
    {0x182F9, 0x9F, 0xFE},
    {0x19D39, 0x9F, 0xFE},
    {0x1A04B, 0x9F, 0xFE},
    {0x1A089, 0x9F, 0xFE},
    {0x1A0D6, 0x9F, 0xFE},
    {0x1A146, 0x9F, 0xFE},
    {0x1A1B9, 0x9F, 0xFE},
    {0x1A2CB, 0x9F, 0xFE},
    {0x1A3A9, 0x9F, 0xFE},
};

// ──────────────────── [1b] 标点禁则 32 处（行首禁则：。、」』 不落行首）────────────────────
//
// sub_437E80 的换行判定里硬编码了 SJIS 四个码位：
//     if (char != 0x8141 && != 0x8142 && != 0x8176 && != 0x8178) 才能换行
// 即「、 。 」 』」不落行首（行首禁则）。
// 该判定被内联了 8 份（sub_437E80 本体 + 7 处副本），共 32 处比较，
// 全部是等长 `mov r32, imm32`（5 字节：opcode + 立即数 4 字节），
// 所以只改立即数的低 2 字节（VA+1、VA+2），指令长度不变。
//
// GBK 对应码位（cp932/gbk 实测对照）：
//     SJIS 0x8141 '、' -> GBK 0xA1A2
//     SJIS 0x8142 '。' -> GBK 0xA1A3
//     SJIS 0x8176 '」' -> GBK 0xA1B9
//     SJIS 0x8178 '』' -> GBK 0xA1BB
// 不改则禁则完全失效，标点会落到行首（台词大量用「」『』包裹，影响明显）。
static const byte_patch k_punct_patches[] = {
    {0x37EE1, 0x41, 0xA2},   /* 8141->A1A2 lo */
    {0x37EE2, 0x81, 0xA1},   /*   hi */
    {0x37EEF, 0x42, 0xA3},   /* 8142->A1A3 lo */
    {0x37EF0, 0x81, 0xA1},   /*   hi */
    {0x37EFD, 0x76, 0xB9},   /* 8176->A1B9 lo */
    {0x37EFE, 0x81, 0xA1},   /*   hi */
    {0x37F0B, 0x78, 0xBB},   /* 8178->A1BB lo */
    {0x37F0C, 0x81, 0xA1},   /*   hi */
    {0x39AD6, 0x41, 0xA2},   /* 8141->A1A2 lo */
    {0x39AD7, 0x81, 0xA1},   /*   hi */
    {0x39AE0, 0x42, 0xA3},   /* 8142->A1A3 lo */
    {0x39AE1, 0x81, 0xA1},   /*   hi */
    {0x39AEA, 0x76, 0xB9},   /* 8176->A1B9 lo */
    {0x39AEB, 0x81, 0xA1},   /*   hi */
    {0x39AF4, 0x78, 0xBB},   /* 8178->A1BB lo */
    {0x39AF5, 0x81, 0xA1},   /*   hi */
    {0x3A837, 0x41, 0xA2},   /* 8141->A1A2 lo */
    {0x3A838, 0x81, 0xA1},   /*   hi */
    {0x3A845, 0x42, 0xA3},   /* 8142->A1A3 lo */
    {0x3A846, 0x81, 0xA1},   /*   hi */
    {0x3A853, 0x76, 0xB9},   /* 8176->A1B9 lo */
    {0x3A854, 0x81, 0xA1},   /*   hi */
    {0x3A861, 0x78, 0xBB},   /* 8178->A1BB lo */
    {0x3A862, 0x81, 0xA1},   /*   hi */
    {0x6336E, 0x41, 0xA2},   /* 8141->A1A2 lo */
    {0x6336F, 0x81, 0xA1},   /*   hi */
    {0x63378, 0x42, 0xA3},   /* 8142->A1A3 lo */
    {0x63379, 0x81, 0xA1},   /*   hi */
    {0x63382, 0x76, 0xB9},   /* 8176->A1B9 lo */
    {0x63383, 0x81, 0xA1},   /*   hi */
    {0x6338C, 0x78, 0xBB},   /* 8178->A1BB lo */
    {0x6338D, 0x81, 0xA1},   /*   hi */
    {0x6390F, 0x41, 0xA2},   /* 8141->A1A2 lo */
    {0x63910, 0x81, 0xA1},   /*   hi */
    {0x63919, 0x42, 0xA3},   /* 8142->A1A3 lo */
    {0x6391A, 0x81, 0xA1},   /*   hi */
    {0x63923, 0x76, 0xB9},   /* 8176->A1B9 lo */
    {0x63924, 0x81, 0xA1},   /*   hi */
    {0x6392D, 0x78, 0xBB},   /* 8178->A1BB lo */
    {0x6392E, 0x81, 0xA1},   /*   hi */
    {0x63FC6, 0x41, 0xA2},   /* 8141->A1A2 lo */
    {0x63FC7, 0x81, 0xA1},   /*   hi */
    {0x63FD0, 0x42, 0xA3},   /* 8142->A1A3 lo */
    {0x63FD1, 0x81, 0xA1},   /*   hi */
    {0x63FDA, 0x76, 0xB9},   /* 8176->A1B9 lo */
    {0x63FDB, 0x81, 0xA1},   /*   hi */
    {0x63FE4, 0x78, 0xBB},   /* 8178->A1BB lo */
    {0x63FE5, 0x81, 0xA1},   /*   hi */
    {0x65875, 0x41, 0xA2},   /* 8141->A1A2 lo */
    {0x65876, 0x81, 0xA1},   /*   hi */
    {0x6587F, 0x42, 0xA3},   /* 8142->A1A3 lo */
    {0x65880, 0x81, 0xA1},   /*   hi */
    {0x65889, 0x76, 0xB9},   /* 8176->A1B9 lo */
    {0x6588A, 0x81, 0xA1},   /*   hi */
    {0x65893, 0x78, 0xBB},   /* 8178->A1BB lo */
    {0x65894, 0x81, 0xA1},   /*   hi */
    {0x65AC8, 0x41, 0xA2},   /* 8141->A1A2 lo */
    {0x65AC9, 0x81, 0xA1},   /*   hi */
    {0x65AD2, 0x42, 0xA3},   /* 8142->A1A3 lo */
    {0x65AD3, 0x81, 0xA1},   /*   hi */
    {0x65ADC, 0x76, 0xB9},   /* 8176->A1B9 lo */
    {0x65ADD, 0x81, 0xA1},   /*   hi */
    {0x65AE6, 0x78, 0xBB},   /* 8178->A1BB lo */
    {0x65AE7, 0x81, 0xA1},   /*   hi */
};

static int write_bytes(u32 address, const u8* data, u32 length)
{
    ulong old;
    u32 at;
    if (!pVirtualProtect) return 0;
    if (!pVirtualProtect((void*)address, length, 0x40 /*PAGE_EXECUTE_READWRITE*/, &old)) return 0;
    for (at = 0; at < length; ++at) ((u8*)address)[at] = data[at];
    pVirtualProtect((void*)address, length, old, &old);
    return 1;
}

static u32 image_size(u32 base)
{
    u8* image = (u8*)base;
    u32 pe;
    if (!base) return 0;
    pe = *(u32*)(image + 0x3C);
    if (*(u32*)(image + pe) != 0x00004550) return 0;
    return *(u32*)(image + pe + 24 + 56);   /* OptionalHeader.SizeOfImage */
}

static void apply_lead_byte_patches(u32 base, u32 size)
{
    unsigned i;
    unsigned applied = 0;
    for (i = 0; i < sizeof(k_lead_byte_patches) / sizeof(k_lead_byte_patches[0]); ++i) {
        const byte_patch* patch = &k_lead_byte_patches[i];
        u8* at;
        if (patch->rva + 1 > size) {          /* 超出主模块镜像：版本不符，跳过 */
            reset_line("lead-byte");
            put("SKIP out-of-image +");
            put_hex(patch->rva, 5);
            flush_line();
            continue;
        }
        at = (u8*)(base + patch->rva);
        if (*at != patch->expected) {
            reset_line("lead-byte");
            put("MISMATCH +");
            put_hex(patch->rva, 5);
            put(" = ");
            put_hex(*at, 2);
            put(" (want ");
            put_hex(patch->expected, 2);
            put(")");
            flush_line();
            continue;
        }
        if (write_bytes((u32)at, &patch->replacement, 1)) ++applied;
    }
    reset_line("lead-byte");
    put("applied ");
    put_dec((int)applied);
    put("/9");
    if (applied != 9) {
        g_enabled = 0;
        put("  -> DISABLED (exe 版本不匹配，整体不应用)");
    }
    flush_line();
}

static void apply_punct_patches(u32 base, u32 size)
{
    unsigned i;
    unsigned applied = 0;
    unsigned skipped = 0;
    for (i = 0; i < sizeof(k_punct_patches) / sizeof(k_punct_patches[0]); ++i) {
        const byte_patch* patch = &k_punct_patches[i];
        u8* at;
        if (patch->rva + 1 > size) {          /* 超出主模块镜像：版本不符 */
            ++skipped;
            continue;
        }
        at = (u8*)(base + patch->rva);
        if (*at != patch->expected) {          /* 期望字节不符：跳过，绝不盲写 */
            ++skipped;
            continue;
        }
        if (write_bytes((u32)at, &patch->replacement, 1)) ++applied;
    }
    reset_line("punct");
    put("applied ");
    put_dec((int)applied);
    put("/64 skipped ");
    put_dec((int)skipped);
    if (applied < 64) put("  (部分未命中：exe 版本漂移，标点禁则可能不完整)");
    flush_line();
}

// ───────────────────────── [2][3] IAT 钩子：CreateFontA ─────────────────────────

#define GB2312_CHARSET 134
#define SHIFTJIS_CHARSET 128

/* 随包字体族名：GBK「思源黑体」。配置显示名与 CreateFontA 用的必须是同一串，否则 GDI 匹配失败会退回西文字体 */
static char g_face[64] = { (char)0xCB,(char)0xBC,(char)0xD4,(char)0xB4,(char)0xBA,(char)0xDA,(char)0xCC,(char)0xE5, 0 };
/* 字体名输入框走双字节配对（原值 "ＭＳ ゴシック" 就是 SJIS），所以配置项里存中文名 */
static const char g_face_display[] = { (char)0xCB,(char)0xBC,(char)0xD4,(char)0xB4,(char)0xBA,(char)0xDA,(char)0xCC,(char)0xE5, 0 };
static int g_force_face = 1;  /* 引擎用默认字体时替换成随包字体 */

typedef HANDLE(__stdcall* FN_CreateFontA)(int, int, int, int, int, u32, u32, u32,
                                          u8, u8, u8, u8, u8, const char*);
static FN_CreateFontA real_CreateFontA;
#ifdef CNPATCH_DEBUG
static long g_font_calls;
#endif

static int is_display_face(const char* face)
{
    int i = 0;
    if (!face) return 0;
    while (g_face_display[i] || face[i]) {
        if (g_face_display[i] != face[i]) return 0;
        ++i;
    }
    return 1;
}

static HANDLE __stdcall hook_CreateFontA(int height, int width, int escapement, int orientation,
                                         int weight, u32 italic, u32 underline, u32 strikeout,
                                         u8 charset, u8 out_precision, u8 clip_precision,
                                         u8 quality, u8 pitch_and_family, const char* face)
{
    HANDLE result;
#ifdef CNPATCH_DEBUG
    u8 before = charset;
#endif

    if (charset == SHIFTJIS_CHARSET || charset == 0) {
        charset = GB2312_CHARSET;
        /* 不劫持字体名：默认值由配置钩子给（只在 FONT 项缺失时），玩家选什么都原样用。
           这里只把「显示名」归一成内部同一串，保证两者永远字面相同。 */
        if (g_enabled && g_force_face && g_face[0] && is_display_face(face)) face = g_face;
    }
#ifdef CNPATCH_DEBUG
    if (g_font_calls < 40) {
        ++g_font_calls;
        reset_line("font");
        put("h=");
        put_dec(height);
        put(" w=");
        put_dec(weight);
        put(" charset ");
        put_hex(before, 2);
        put("->");
        put_hex(charset, 2);
        put(" face=");
        put(face ? face : "(null)");
        flush_line();
    }
#endif
    result = real_CreateFontA(height, width, escapement, orientation, weight, italic, underline,
                              strikeout, charset, out_precision, clip_precision, quality,
                              pitch_and_family, face);
    return result;
}

// 在 exe 的导入表里把 GDI32!CreateFontA 换成我们的钩子，返回原函数指针。
// 导入表结构（PE32）：IMAGE_IMPORT_DESCRIPTOR{OriginalFirstThunk, TimeDateStamp,
// ForwarderChain, Name, FirstThunk}，thunk 数组元素为 IMAGE_IMPORT_BY_NAME 的 RVA
// （高位为 1 表示按序号导入）。
static FN_CreateFontA hook_import(u32 base, const char* dll, const char* name, void* replacement)
{
    u8* image = (u8*)base;
    u32 pe = *(u32*)(image + 0x3C);
    u32 descriptors = *(u32*)(image + pe + 24 + 96 + 8 * 1);   /* DataDirectory[1] = 导入表 RVA */
    u32 descriptor;

    if (!descriptors) return 0;
    for (descriptor = descriptors; *(u32*)(image + descriptor + 12); descriptor += 20) {
        u32 lookup = *(u32*)(image + descriptor + 0);
        u32 iat = *(u32*)(image + descriptor + 16);
        u32 dll_name = *(u32*)(image + descriptor + 12);
        u32 index;

        if (!eq_ci((const char*)(image + dll_name), dll)) continue;
        if (!lookup) lookup = iat;
        for (index = 0;; ++index) {
            u32 entry = *(u32*)(image + lookup + index * 4);
            FN_CreateFontA original;
            if (!entry) return 0;
            if (entry & 0x80000000u) continue;                     /* 按序号导入 */
            if (!eq_ci((const char*)(image + entry + 2), name)) continue;
            original = (FN_CreateFontA)(*(u32*)(image + iat + index * 4));
            {
                u32 value = (u32)replacement;
                if (!write_bytes((u32)(image + iat + index * 4), (const u8*)&value, 4)) return 0;
            }
            return original;
        }
    }
    return 0;
}

// ───────────────────────── [3] 私有字体注册 ─────────────────────────

/* 拼出 "cnpatch\<sub>\<name>" 形式的绝对路径；sub 可为空。返回长度，失败返回 0 */
static int build_path(char* out, int size, const char* sub, const char* name)
{
    int n = 0, k = 0;
    char* tail;
    char* scan;

    if (!pGetModuleFileNameA || !pGetModuleFileNameA(g_self, out, 460)) return 0;
    tail = out;
    for (scan = out; *scan; ++scan) if (*scan == 92) tail = scan;
    *tail = 0;
    while (out[n]) ++n;
    if (sub && sub[0]) {
        out[n++] = (char)92;
        while (sub[k] && n < size - 8) out[n++] = sub[k++];
    }
    if (name) {
        out[n++] = (char)92;
        k = 0;
        while (name[k] && n < size - 2) out[n++] = name[k++];
    }
    out[n] = 0;
    return n;
}

/* 只认 .ttf（大小写不敏感）。
   不收 .otf：OTF/CFF 轮廓在 EnumFontFamiliesExA 里是 fonttype=2(DEVICE)，
   而游戏的字体列表只收 fonttype=4(TRUETYPE)，注册了也选不到。
   注册成功却不出现，只会让用户以为加载了而在列表里找不到，不如直接不注册。
   详见 doc/engine-mechanics.md §11。 */
static int is_font_file(const char* name)
{
    u32 n = 0;
    while (name[n]) ++n;
    if (n < 5) return 0;
    if (name[n - 4] != '.') return 0;
    return eq_ci(name + n - 3, "ttf");
}

/* 注册单个字体文件，返回 AddFontResourceExA 的结果（face 数量，0 = 失败） */
static int register_font_file(const char* sub, const char* name)
{
    static char path[512];
    int added;
    if (!pAddFontResourceExA) return 0;
    if (!build_path(path, (int)sizeof(path), sub, name)) return 0;
    added = pAddFontResourceExA(path, FR_PRIVATE, 0);
    reset_line("font-file");
    put(path);
    put(" -> ");
    put_dec(added);
    flush_line();
    return added;
}

/* 扫 cnpatch/font 下的 *.ttf 全部注册：用户把自己的字体丢进去就能在游戏设置页选中。
   注册不需要知道族名（选中是对用户操作的响应），所以这里不解析 sfnt name 表。
   族名中英文都可以（实测 ASCII 名也能正常选中），详见 doc/engine-mechanics.md §11。
   默认脸仍是随包的「思源黑体」。 */
static void add_private_font(void)
{
    static FIND_DATA_A data;
    static char pattern[512];
    void* handle;
    u32 count = 0, failed = 0;

    if (pFindFirstFileA && pFindNextFileA && pFindClose) {
        if (build_path(pattern, (int)sizeof(pattern), "font", "*.*")) {
            handle = pFindFirstFileA(pattern, &data);
            if (handle != (void*)-1 && handle) {
                do {
                    if (is_font_file(data.name)) {
                        if (register_font_file("font", data.name)) ++count;
                        else ++failed;
                    }
                } while (pFindNextFileA(handle, &data));
                pFindClose(handle);
            }
        }
    }
    reset_line("font-dir");
    put("registered ");
    put_dec((int)count);
    put(" failed ");
    put_dec((int)failed);
    flush_line();
}

// ───────────────────────── 选项（环境变量） ─────────────────────────

static void read_env(const char* name, char* buffer, u32 size)
{
    u32 k32 = module_base("kernel32.dll");
    FN_GetEnvironmentVariableA fn = (FN_GetEnvironmentVariableA)export_lookup(k32, "GetEnvironmentVariableA");
    buffer[0] = 0;
    if (fn) fn(name, buffer, size);
}

static void load_options(void)
{
    char value[64];
    read_env("AOITORI_FACE", value, sizeof(value));
    if (value[0]) {
        u32 i = 0;
        while (value[i] && i < 60) { g_face[i] = value[i]; ++i; }
        g_face[i] = 0;
    }
    read_env("AOITORI_CHARSET_ONLY", value, sizeof(value));
    if (value[0] == '1') g_force_face = 0;
    read_env("AOITORI_NOPATCH", value, sizeof(value));
    if (value[0] == '1') g_force_face = 0;
}


// ── 默认字体名：就地改引擎里的默认串 ──
//
// 引擎没有「默认配置文件」，默认字体名是两处写死的数据：
//   0x4D1AD4  .rdata 里的 ASCII 字面量 "MS Gothic"（sub_461840 在某个分支 lstrcpyA 到下面那个全局）
//   0x4E9670  .data  里 FONT 设置全局的初始值 = SJIS 全角 "ＭＳ ゴシック"（14 字节 + NUL）
// 默认项缺失时引擎就是拿这些值填 FONT 设置，设置界面显示的也是它。
//
// 把这两处就地换成 GBK「思源黑体」（8 字节 + NUL），引擎自己就会把默认项初始化成随包字体：
// 不需要读配置钩子、不需要每次启动判断，也没有禁用任何字体 ——
// 玩家在设置页选了什么、cmvs.cfg 里存了什么，引擎读到的就是什么（实测见 doc §10）。
//
// 空间够：SJIS 全角名占 15 字节，ASCII 名占 10 字节，GBK 名只占 9 字节，余下补 0。
static const u8 kFaceGb2312[9] = { 0xCB,0xBC,0xD4,0xB4,0xBA,0xDA,0xCC,0xE5, 0 };

typedef struct {
    u32 address;
    u8 expect[15];
    u32 length;
} DEFAULT_FACE_PATCH;

static const DEFAULT_FACE_PATCH kDefaultFacePatches[] = {
    /* 真正的 lpDefault：sub_41D490 里 GetPrivateProfileStringA("MESSAGE_INFO","FONT", 这一串) */
    { 0x4D0BF8u, { 0x82,0x6C,0x82,0x72,0x20,0x83,0x53,0x83,0x56,0x83,0x62,0x83,0x4E, 0 }, 15 },
    /* 默认脸名的权威副本：sub_419BF0 就是把它 lstrcpyA 进字体对象（sub_461840 里用到） */
    { 0x4E9298u, { 0x82,0x6C,0x82,0x72,0x20,0x83,0x53,0x83,0x56,0x83,0x62,0x83,0x4E, 0 }, 15 },
    /* ASCII "MS Gothic" */
    { 0x4D1AD4u, { 'M','S',' ','G','o','t','h','i','c', 0 }, 10 },
    /* SJIS 全角 "ＭＳ ゴシック"（中间是 ASCII 空格 0x20，不是全角空格） */
    { 0x4E9670u, { 0x82,0x6C,0x82,0x72,0x20,0x83,0x53,0x83,0x56,0x83,0x62,0x83,0x4E, 0 }, 15 },
    /* 字体列表里比对「当前选中项」用的副本，改掉后列表才会把随包字体标成当前项 */
    { 0x4E97B0u, { 0x82,0x6C,0x82,0x72,0x20,0x83,0x53,0x83,0x56,0x83,0x62,0x83,0x4E, 0 }, 15 },
};

static int patch_default_face(void)
{
    static u8 buffer[16];
    static int done;
    u32 i, k;
    int ok = 0;
    if (done) return done;                          /* 已打过，避免重复写 */
    for (i = 0; i < sizeof(kDefaultFacePatches) / sizeof(kDefaultFacePatches[0]); ++i) {
        const DEFAULT_FACE_PATCH* patch = &kDefaultFacePatches[i];
        const u8* at = (const u8*)patch->address;
        int match = 1;
        for (k = 0; k < patch->length; ++k) {
            if (at[k] != patch->expect[k]) { match = 0; break; }
        }
        if (!match) {
            /* 诊断：把实际字节打出来，方便判断是版本变化还是字节被引擎先改了 */
            reset_line("font-default");
            put("mismatch 0x");
            put_hex(patch->address, 6);
            put(" actual=");
            for (k = 0; k < patch->length && k < 12; ++k) { put_hex(at[k], 2); put(" "); }
            flush_line();
            continue;                               /* 版本不匹配：这一处一个字都不动 */
        }
        for (k = 0; k < sizeof(buffer); ++k) buffer[k] = 0;
        for (k = 0; k < sizeof(kFaceGb2312); ++k) buffer[k] = kFaceGb2312[k];
        if (write_bytes(patch->address, buffer, patch->length)) ++ok;
    }
    done = ok;
    return ok;
}

typedef int(__stdcall* FN_EnumFontFamiliesExA)(void*, void*, void*, void*, u32);
static FN_EnumFontFamiliesExA real_EnumFontFamiliesExA;

/* 字体选择列表：引擎用 exe 里的 FONT_SHIFT_JIS 决定 lfCharSet(0x80)，
   所以只列日文字体。这里强制成 GB2312(134)，我们的字体与其它中文字体才会出现。 */
static int __stdcall hook_EnumFontFamiliesExA(void* dc, void* logfont, void* callback, void* param, u32 flags)
{
    if (logfont) ((u8*)logfont)[0x17] = GB2312_CHARSET;   /* LOGFONTA.lfCharSet */
    if (!real_EnumFontFamiliesExA) return 0;
    return real_EnumFontFamiliesExA(dc, logfont, callback, param, flags);
}


// ── 字体是否真的进入本进程的 GDI 字体表 ──
// AddFontResourceExA 返回非 0 不代表字体已可用；这里在注册后立刻真实枚举一次。

typedef int(__stdcall* FN_EnumFontFamiliesExA2)(void*, void*, int(__stdcall*)(void*, void*, u32, long), long, u32);
typedef void*(__stdcall* FN_CreateThread)(void*, u32, u32(__stdcall*)(void*), void*, u32, u32*);
typedef u32(__stdcall* FN_Sleep)(u32);
static FN_CreateThread pCreateThread;
static FN_Sleep pSleep;


static u32 g_enum_found;

static int __stdcall enum_proc(const void* logfont, const void* textmetric, u32 fonttype, long param)
{
    const char* face = (const char*)logfont + 28;   /* LOGFONTA.lfFaceName */
    int i = 0;
    (void)textmetric; (void)fonttype; (void)param;
    ++g_enum_count;
    while (face[i] || g_face_display[i]) {
        if (face[i] != g_face_display[i]) return 1;
        ++i;
    }
    g_enum_found = 1;
    return 1;
}

static void verify_font_enum(void)
{
    u8 logfont[92];            /* LOGFONTA */
    u32 gdi32 = module_base("gdi32.dll");
    FN_EnumFontFamiliesExA2 enumerate = (FN_EnumFontFamiliesExA2)export_lookup(gdi32, "EnumFontFamiliesExA");
    void* dc;
    u32 i;

    if (!enumerate) { reset_line("font-enum"); put("no EnumFontFamiliesExA"); flush_line(); return; }
    dc = pGetDC ? pGetDC(0) : 0;
    if (!dc) { reset_line("font-enum"); put("no DC"); flush_line(); return; }
    for (i = 0; i < sizeof(logfont); ++i) logfont[i] = 0;
    logfont[0x17] = GB2312_CHARSET;             /* lfCharSet */
    g_enum_count = 0;
    g_enum_found = 0;
    enumerate(dc, logfont, (void*)enum_proc, 0, 0);   /* GDI 这里是 const LOGFONT* */
    pReleaseDC(0, dc);
    reset_line("font-enum");
    put("charset134 count=");
    put_dec((int)g_enum_count);
    put(" ours=");
    put(g_enum_found ? "FOUND" : "NOTFOUND");
    flush_line();
}

static u32 __stdcall font_thread(void* unused)
{
    int i;
    (void)unused;
    for (i = 0; i < 2000; ++i) {
        if (module_base("gdi32.dll")) break;
        if (pSleep) pSleep(1);
    }
    if (pSleep) pSleep(30);          /* 让出初始化阶段 */
    add_private_font();
    verify_font_enum();
    return 0;
}

// ── 标题栏：修 SJIS 全角空格 + 换成中文标题 ──
// start.ps3 在 data/update 挂载之前就被读入（data/update 正是 start.ps3 自己挂上去的），
// 所以标题永远来自 data/code/start.ps3 的那串 "Aoi Tori[ver.1.03] R18"；引擎拼接时还会
// 插一个 SJIS 全角空格 0x81 0x40，在 GBK 下解成乱码字（历史截图里的 "R18号"）。

typedef int(__stdcall* FN_SetWindowTextA)(void*, const char*);
static FN_SetWindowTextA real_SetWindowTextA;

static int starts_with(const char* text, const char* prefix)
{
    while (*prefix) { if (*text++ != *prefix++) return 0; }
    return 1;
}

/* 返回写出的字节数；out 里是 GBK 文本 */
static int fix_title(char* out, int size, const char* src)
{
    /* 青鸟【简体中文】 的 GBK 字节 */
    static const char title[] = { (char)0xC7,(char)0xE0,(char)0xC4,(char)0xF1,
                                  (char)0xA1,(char)0xBE,(char)0xBC,(char)0xF2,
                                  (char)0xCC,(char)0xE5,(char)0xD6,(char)0xD0,
                                  (char)0xCE,(char)0xC4,(char)0xA1,(char)0xBF, 0 };
    static const char prefix[] = "Aoi Tori[ver.1.03] R18";
    const char* at;
    int n = 0, i;

    if (starts_with(src, prefix)) {
        at = src + sizeof(prefix) - 1;                /* 按前缀长度跳过，保留 " - 章节名" */
        for (i = 0; title[i] && n < size - 1; ++i) out[n++] = title[i];
    } else {
        at = src;
    }
    while (*at && n < size - 3) {
        if ((u8)at[0] == 0x81 && (u8)at[1] == 0x40) { /* SJIS 全角空格 → GBK 全角空格 */
            out[n++] = (char)0xA1;
            out[n++] = (char)0xA1;
            at += 2;
            continue;
        }
        out[n++] = *at++;
    }
    out[n] = 0;
    return n;
}

static int __stdcall hook_SetWindowTextA(void* window, const char* text)
{
    static char buffer[512];
    if (!text) return real_SetWindowTextA ? real_SetWindowTextA(window, text) : 0;
    fix_title(buffer, (int)sizeof(buffer), text);
    reset_line("title");
    put("-> ");
    put(buffer);
    flush_line();
    return real_SetWindowTextA ? real_SetWindowTextA(window, buffer) : 0;
}

// ───────────────────────── 入口 ─────────────────────────

int __stdcall DllMain(HMODULE self, u32 reason, void* reserved)
{
    u32 k32, gdi32, base;
    char skip[8];
    (void)reserved;

    if (reason != DLL_PROCESS_ATTACH) return 1;
    g_self = self;

    k32 = module_base("kernel32.dll");
    gdi32 = module_base("gdi32.dll");
    if (!k32) return 1;

    pLoadLibraryA = (FN_LoadLibraryA)export_lookup(k32, "LoadLibraryA");
    pGetProcAddress = (FN_GetProcAddress)export_lookup(k32, "GetProcAddress");
    pVirtualProtect = (FN_VirtualProtect)export_lookup(k32, "VirtualProtect");
    pCreateFileA = (FN_CreateFileA)export_lookup(k32, "CreateFileA");
    pWriteFile = (FN_WriteFile)export_lookup(k32, "WriteFile");
    pGetModuleFileNameA = (FN_GetModuleFileNameA)export_lookup(k32, "GetModuleFileNameA");
    pCloseHandle = (FN_CloseHandle)export_lookup(k32, "CloseHandle");
    pSetFilePointer = (FN_SetFilePointer)export_lookup(k32, "SetFilePointer");
    if (gdi32) pAddFontResourceExA = (FN_AddFontResourceExA)export_lookup(gdi32, "AddFontResourceExA");
    {
        u32 user32 = module_base("user32.dll");   /* GetDC / ReleaseDC 在 user32 */
        pGetDC = (FN_GetDC)export_lookup(user32, "GetDC");
        pReleaseDC = (FN_ReleaseDC)export_lookup(user32, "ReleaseDC");
    }
    pCreateThread = (FN_CreateThread)export_lookup(k32, "CreateThread");
    pSleep = (FN_Sleep)export_lookup(k32, "Sleep");
    pFindFirstFileA = (FN_FindFirstFileA)export_lookup(k32, "FindFirstFileA");
    pFindNextFileA = (FN_FindNextFileA)export_lookup(k32, "FindNextFileA");
    pFindClose = (FN_FindClose)export_lookup(k32, "FindClose");

    base = peb() ? peb()->image_base_address : 0;
    reset_line("init");
    put("exe=");
    put_hex(base, 8);
    put(" kernel32=");
    put_hex(k32, 8);
    put(" gdi32=");
    put_hex(gdi32, 8);
    flush_line();

    if (!base) return 1;
    load_options();
    read_env("AOITORI_NOPATCH", skip, sizeof(skip));
    if (skip[0] == '1') return 1;

    {
        u32 size = image_size(base);
        reset_line("image");
        put("base=");
        put_hex(base, 8);
        put(" size=");
        put_hex(size, 8);
        flush_line();
        apply_lead_byte_patches(base, size);
        apply_punct_patches(base, size);
    }

    real_CreateFontA = hook_import(base, "GDI32.dll", "CreateFontA", (void*)hook_CreateFontA);
    real_EnumFontFamiliesExA = (FN_EnumFontFamiliesExA)hook_import(base, "GDI32.dll", "EnumFontFamiliesExA",
                                                                  (void*)hook_EnumFontFamiliesExA);
    real_SetWindowTextA = (FN_SetWindowTextA)hook_import(base, "USER32.dll", "SetWindowTextA",
                                                         (void*)hook_SetWindowTextA);
    reset_line("iat");
    put("SetWindowTextA ");
    put(real_SetWindowTextA ? "ok" : "FAILED");
    put(" EnumFontFamiliesExA ");
    put(real_EnumFontFamiliesExA ? "ok" : "FAILED");
    flush_line();
    reset_line("iat");
    put("CreateFontA hook ");
    put(real_CreateFontA ? "ok, original=" : "FAILED");
    if (real_CreateFontA) put_hex((u32)real_CreateFontA, 8);
    flush_line();

    if (!g_enabled) { reset_line("gate"); put("skipped: exe mismatch"); flush_line(); return 1; }

    /* 默认字体名（.rdata 字面量）——「改默认设置项」的唯一动作。
       注意：发布版也要执行，只是不写日志，所以调用不能放进日志宏里。 */
    {
        int patched = patch_default_face();
#ifdef CNPATCH_DEBUG
        reset_line("font-default");
        put("patched ");
        put_dec(patched);
        put("/5 -> ");
        put(g_face);                              /* 日志是 GBK，中文一律用同一串字节 */
        put(" (4d0bf8 def, 4e9298, 4d1ad4, 4e9670, 4e97b0)");
        flush_line();
#else
        (void)patched;
#endif
    }
    add_private_font();        /* 先注册一次：赶在引擎建字体表之前 */
    if (pCreateThread) pCreateThread(0, 0, font_thread, 0, 0, 0);
    else verify_font_enum();
    return 1;
}
