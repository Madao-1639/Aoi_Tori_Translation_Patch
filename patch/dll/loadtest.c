// 32 位离线测试桩：加载同目录的 mog3x32.dll（我们的代理），把结果写 dlltest.log。
// 只用于开发期验证代理/补丁的逻辑，不进入发布包。
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long ulong;
typedef void* HMODULE;
typedef void* HANDLE;

typedef struct { u16 length, maximum_length; u16* buffer; } UNICODE_STRING;
typedef struct { u32 length, initialized, ss_handle, in_load_order; } PEB_LDR_DATA;
typedef struct {
    u8 inherited_address_space, read_image_file_exec_options, being_debugged, bit_field;
    u32 mutant, image_base_address; PEB_LDR_DATA* ldr;
} PEB;

#define LDR_DLL_BASE 0x14
#define LDR_BASE_DLL_NAME 0x28

static PEB* peb(void) { PEB* v; __asm__ __volatile__("movl %%fs:0x30, %0" : "=r"(v)); return v; }
static int upper(int c) { return (c >= 'a' && c <= 'z') ? c - 32 : c; }
static int eq_ci(const char* a, const char* b)
{ while (*a && *b) { if (upper((u8)*a) != upper((u8)*b)) return 0; ++a, ++b; } return *a == *b; }
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
typedef u32(__stdcall* FN_GetLastError)(void);
typedef HANDLE(__stdcall* FN_CreateFileA)(const char*, u32, u32, void*, u32, u32, HANDLE);
typedef int(__stdcall* FN_WriteFile)(HANDLE, const void*, u32, u32*, void*);
typedef int(__stdcall* FN_CloseHandle)(HANDLE);
typedef u32(__stdcall* FN_GetModuleFileNameA)(HMODULE, char*, u32);

static char g_buf[512];
static int g_at;

static void put(const char* text) { while (*text && g_at < 480) g_buf[g_at++] = *text++; }
static void put_hex(u32 value, int digits)
{
    static const char hex[] = "0123456789abcdef"; int i;
    for (i = digits - 1; i >= 0; --i) if (g_at < 490) g_buf[g_at++] = hex[(value >> (4 * i)) & 0xF];
}

void entry(void)
{
    u32 k32 = module_base("kernel32.dll");
    FN_LoadLibraryA load = (FN_LoadLibraryA)export_lookup(k32, "LoadLibraryA");
    FN_GetLastError last_error = (FN_GetLastError)export_lookup(k32, "GetLastError");
    FN_CreateFileA create = (FN_CreateFileA)export_lookup(k32, "CreateFileA");
    FN_WriteFile write = (FN_WriteFile)export_lookup(k32, "WriteFile");
    FN_CloseHandle close = (FN_CloseHandle)export_lookup(k32, "CloseHandle");
    FN_GetModuleFileNameA get_name = (FN_GetModuleFileNameA)export_lookup(k32, "GetModuleFileNameA");
    char path[480];
    HANDLE file;
    HMODULE handle;
    u32 written;
    int n = 0;
    char* tail;

    put("kernel32="); put_hex(k32, 8);
    put(" LoadLibraryA="); put_hex((u32)load, 8);
    put("; ");
    if (load) load("gdi32.dll");   /* 模拟游戏：gdi32 已在模块表里 */
    handle = load ? load("mog3x32.dll") : 0;
    { typedef u32(__stdcall* FN_Sleep)(u32); FN_Sleep sleep_fn = (FN_Sleep)export_lookup(k32, "Sleep");
      if (sleep_fn) sleep_fn(400); }
    put("LoadLibrary(mog3x32.dll)="); put_hex((u32)handle, 8);
    put(" err="); put_hex(last_error ? last_error() : 0, 8);
    g_buf[g_at++] = '\r'; g_buf[g_at++] = '\n';

    if (!get_name || !get_name(0, path, 460)) return;
    tail = path;
    { char* scan; for (scan = path; *scan; ++scan) if (*scan == '\\') tail = scan; }
    *tail = 0;
    { const char* suffix = "\\dlltest.log"; int i = 0;
      while (path[n]) ++n;
      while (suffix[i] && n < 470) path[n++] = suffix[i++]; }
    path[n] = 0;
    file = create(path, 0x40000000u, 3, 0, 4, 0x80, 0);
    if (file == (HANDLE)-1) return;
    write(file, g_buf, (u32)g_at, &written, 0);
    close(file);
}
