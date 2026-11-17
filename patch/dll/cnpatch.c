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
typedef int(__stdcall* FN_GetModuleHandleA)(const char*);

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

// ───────────────────────── 日志 ─────────────────────────

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
    for (scan = path; *scan; ++scan) if (*scan == '\\') tail = scan;
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

// ───────────────────────── [1] 双字节判定 9 处 ─────────────────────────

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
    flush_line();
}

// ───────────────────────── [2][3] IAT 钩子：CreateFontA ─────────────────────────

#define GB2312_CHARSET 134
#define SHIFTJIS_CHARSET 128

static char g_face[64] = "AoiToriCN";
static int g_force_face = 1;

typedef HANDLE(__stdcall* FN_CreateFontA)(int, int, int, int, int, u32, u32, u32,
                                          u8, u8, u8, u8, u8, const char*);
static FN_CreateFontA real_CreateFontA;
static long g_font_calls;

static HANDLE __stdcall hook_CreateFontA(int height, int width, int escapement, int orientation,
                                         int weight, u32 italic, u32 underline, u32 strikeout,
                                         u8 charset, u8 out_precision, u8 clip_precision,
                                         u8 quality, u8 pitch_and_family, const char* face)
{
    HANDLE result;
    u8 before = charset;

    if (charset == SHIFTJIS_CHARSET || charset == 0) {
        charset = GB2312_CHARSET;
        if (g_force_face && g_face[0]) face = g_face;
    }
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

static void add_private_font(void)
{
    static char path[512];
    int n = 0;
    char* tail;
    char* scan;
    const char* suffix = "\\font\\AoiToriCN.ttf";
    int added;

    if (!pAddFontResourceExA || !pGetModuleFileNameA) return;
    if (!pGetModuleFileNameA(g_self, path, 460)) return;
    tail = path;
    for (scan = path; *scan; ++scan) if (*scan == '\\') tail = scan;
    *tail = 0;
    while (path[n] && n < 460) ++n;
    {
        int i = 0;
        while (suffix[i] && n < 500) path[n++] = suffix[i++];
    }
    path[n] = 0;
    added = pAddFontResourceExA(path, FR_PRIVATE, 0);
    reset_line("font-file");
    put(path);
    put(" -> ");
    put_dec(added);
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
    }

    real_CreateFontA = hook_import(base, "GDI32.dll", "CreateFontA", (void*)hook_CreateFontA);
    reset_line("iat");
    put("CreateFontA hook ");
    put(real_CreateFontA ? "ok, original=" : "FAILED");
    if (real_CreateFontA) put_hex((u32)real_CreateFontA, 8);
    flush_line();

    add_private_font();
    return 1;
}
