// mog3x32.dll —— 代理（19 个导出原样转发给改名后的原 DLL），并加载真正的补丁
// data/update/cnpatch.dll。
//
// 为什么选这个文件做注入点：cmvs32.exe 静态导入 mog3x32.dll（Purple 的 OGG/MOG 音频库，
// 321 KB，只依赖 KERNEL32）。Windows 与 Wine 都优先从 exe 所在目录加载它，是「不改 exe、
// Steam 直启」这条路上唯一无条件生效的入口：
//   * dinput8 / DSOUND 只在 JOYPAD_USE / 音频后端特定分支才 LoadLibrary（条件不可靠）；
//   * version/winmm/gdi32 等系统 DLL 在 KnownDLLs 里，目录劫持无效；
//   * dxgi/d3d11 在 Wine 下默认走 builtin，Proton 上会被忽略。
//
// 转发用裸 `jmp [ptr]`：参数、返回值、调用约定全部透传，不改变任何行为。
// 日志写 <代理目录>\proxy.log（追加），只用于开发期诊断。

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef void* HMODULE;

typedef struct { u16 length, maximum_length; u16* buffer; } UNICODE_STRING;
typedef struct { u32 length, initialized, ss_handle, in_load_order; } PEB_LDR_DATA;
typedef struct {
    u8 inherited_address_space, read_image_file_exec_options, being_debugged, bit_field;
    u32 mutant, image_base_address;
    PEB_LDR_DATA* ldr;
} PEB;

#define DLL_PROCESS_ATTACH 1
#define FILE_APPEND_DATA 0x0004u
#define OPEN_ALWAYS 4
#define FILE_ATTRIBUTE_NORMAL 0x80

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
        /* BaseDllName 相对 DllBase 恒为 +0x14；不同 Windows 版本里它在 0x24/0x28/0x2C。 */
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
    u32 pe, exports, count, names, functions, ordinals, size, i;
    if (!module) return 0;
    image = (u8*)module;
    pe = *(u32*)(image + 0x3C);
    if (*(u32*)(image + pe) != 0x00004550) return 0;
    exports = *(u32*)(image + pe + 24 + 96);
    size = *(u32*)(image + pe + 24 + 96 + 4);
    if (!exports) return 0;
    count = *(u32*)(image + exports + 24);
    names = *(u32*)(image + exports + 32);
    functions = *(u32*)(image + exports + 28);
    ordinals = *(u32*)(image + exports + 36);
    if (!names || !functions || !count || count > 65536) return 0;
    for (i = 0; i < count; ++i) {
        u32 entry_name = *(u32*)(image + names + 4 * i);
        if (!entry_name || !eq_ci((const char*)(image + entry_name), name)) continue;
        {
            u16 ordinal = *(u16*)(image + ordinals + 2 * i);
            u32 function = *(u32*)(image + functions + 4 * ordinal);
            if (function >= exports && function < exports + size) return 0;  /* forwarder */
            return module + function;
        }
    }
    return 0;
}

typedef HMODULE(__stdcall* FN_LoadLibraryA)(const char*);
typedef void*(__stdcall* FN_GetProcAddress)(HMODULE, const char*);
typedef u32(__stdcall* FN_GetModuleFileNameA)(HMODULE, char*, u32);
typedef void*(__stdcall* FN_CreateFileA)(const char*, u32, u32, void*, u32, u32, void*);
typedef int(__stdcall* FN_WriteFile)(void*, const void*, u32, u32*, void*);
typedef int(__stdcall* FN_CloseHandle)(void*);
typedef u32(__stdcall* FN_GetLastError)(void);
typedef void*(__stdcall* FN_CreateThread)(void*, u32, u32(__stdcall*)(void*), void*, u32, u32*);
typedef u32(__stdcall* FN_Sleep)(u32);

static FN_LoadLibraryA pLoadLibraryA;
static FN_GetProcAddress pGetProcAddress;
static FN_CreateFileA pCreateFileA;
static FN_WriteFile pWriteFile;
static FN_CloseHandle pCloseHandle;
static FN_GetLastError pGetLastError;
static FN_CreateThread pCreateThread;
static FN_Sleep pSleep;

static HMODULE g_self;
static char g_dir[480];
static char g_patch_path[512];
static HMODULE g_patch;

// ── 19 个转发 ──
#define FORWARDERS(X) \
    X(MGV_Close) X(MGV_CloseCoder) X(MGV_GetFrameDecode) X(MGV_GetOggFileMemory) \
    X(MGV_Init) X(MGV_InputFile) X(MGV_InputMemory) \
    X(MOG_Close) X(MOG_CloseCoder) X(MOG_DetectConfigure) X(MOG_GetPcmFormat) \
    X(MOG_GetPcmLength) X(MOG_GetProcessFrame) X(MOG_Init) X(MOG_InputFile) \
    X(MOG_InputFile2) X(MOG_InputMemory) X(MOG_InputMemory2) X(MOG_Replay)

#define DECLARE_PTR(name) void* p_##name;
FORWARDERS(DECLARE_PTR)
#undef DECLARE_PTR

#define DECLARE_STUB(name) \
    __asm__(".globl _" #name "\n_" #name ":\n\tjmp *_p_" #name "\n");
FORWARDERS(DECLARE_STUB)
#undef DECLARE_STUB

// ── 日志 ──
static void log_write(const char* tag, const char* text, u32 value, int has_value)
{
    static char buffer[900];
    static char path[512];
    static const char hex[] = "0123456789abcdef";
    int n = 0, length = 0, i;
    void* file;
    u32 written;

    if (!pCreateFileA || !pWriteFile || !g_dir[0]) return;
    while (g_dir[n]) { path[n] = g_dir[n]; ++n; }
    {
        const char* suffix = "\\proxy.log";
        int k = 0;
        while (suffix[k]) path[n++] = suffix[k++];
    }
    path[n] = 0;

    for (i = 0; tag[i] && length < 200; ++i) buffer[length++] = tag[i];
    buffer[length++] = '\t';
    if (has_value) {
        for (i = 7; i >= 0; --i) buffer[length++] = hex[(value >> (4 * i)) & 0xF];
    } else {
        for (i = 0; text && text[i] && length < 800; ++i) buffer[length++] = text[i];
    }
    buffer[length++] = '\r';
    buffer[length++] = '\n';

    file = pCreateFileA(path, FILE_APPEND_DATA, 3, 0, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, 0);
    if (file == (void*)-1) return;
    pWriteFile(file, buffer, (u32)length, &written, 0);
    pCloseHandle(file);
}

static void log_value(const char* tag, u32 value) { log_write(tag, 0, value, 1); }
static void log_text(const char* tag, const char* text) { log_write(tag, text, 0, 0); }

// ── 加载补丁本体；失败时开线程等 gdi32 就绪后重试 ──
static HMODULE try_load_patch(void)
{
    for (;;) {
        HMODULE handle;
        u32 error;
        if (!g_patch_path[0]) return 0;
        handle = pLoadLibraryA(g_patch_path);
        error = pGetLastError ? pGetLastError() : 0;
        if (handle) {
            log_value("cnpatch.ok", (u32)handle);
            return handle;
        }
        log_value("cnpatch.err", error);
        return 0;
    }
}

static u32 __stdcall retry_thread(void* unused)
{
    int i;
    (void)unused;
    for (i = 0; i < 3000; ++i) {
        if (module_base("gdi32.dll")) break;
        if (pSleep) pSleep(1);
    }
    g_patch = pLoadLibraryA(g_patch_path);
    { u32 err = pGetLastError ? pGetLastError() : 0;
      log_value("cnpatch.retry", (u32)g_patch);
      log_value("cnpatch.retry.err", err);
      if (!g_patch) {
          HMODULE kernel = pLoadLibraryA(g_patch_path);
          u32 err2 = pGetLastError ? pGetLastError() : 0;
          log_value("cnpatch.retry2", (u32)kernel);
          log_value("cnpatch.retry2.err", err2);
      } }
}

static void dir_of_module(HMODULE self, FN_GetModuleFileNameA get_name)
{
    char* tail;
    char* scan;
    g_dir[0] = 0;
    if (!get_name || !get_name(self, g_dir, 460)) return;
    tail = g_dir;
    for (scan = g_dir; *scan; ++scan) if (*scan == '\\') tail = scan;
    *tail = 0;
}

int __stdcall DllMain(HMODULE self, u32 reason, void* reserved)
{
    static char path[512];
    u32 k32;
    HMODULE original;
    (void)reserved;

    if (reason != DLL_PROCESS_ATTACH) return 1;
    g_self = self;

    k32 = module_base("kernel32.dll");
    pLoadLibraryA = (FN_LoadLibraryA)export_lookup(k32, "LoadLibraryA");
    pGetProcAddress = (FN_GetProcAddress)export_lookup(k32, "GetProcAddress");
    pCreateFileA = (FN_CreateFileA)export_lookup(k32, "CreateFileA");
    pWriteFile = (FN_WriteFile)export_lookup(k32, "WriteFile");
    pCloseHandle = (FN_CloseHandle)export_lookup(k32, "CloseHandle");
    pGetLastError = (FN_GetLastError)export_lookup(k32, "GetLastError");
    pCreateThread = (FN_CreateThread)export_lookup(k32, "CreateThread");
    pSleep = (FN_Sleep)export_lookup(k32, "Sleep");
    dir_of_module(self, (FN_GetModuleFileNameA)export_lookup(k32, "GetModuleFileNameA"));
    log_text("dir", g_dir);
    log_value("self.base", (u32)self);
    if (!pLoadLibraryA || !pGetProcAddress) return 0;

    // 1) 接管原音频库，保证 MOG_* 可用
    {
        int n = 0, k = 0;
        static const char suffix[] = { 92,'m','o','g','3','x','3','2','.','d','l','l','.','o','r','i','g',0 };
        while (g_dir[n]) { path[n] = g_dir[n]; ++n; }
        while (suffix[k]) path[n++] = suffix[k++];
        path[n] = 0;
        original = pLoadLibraryA(path);
        log_value("orig", (u32)original);
        if (!original) return 0;
    }
#define RESOLVE_PTR(name) p_##name = pGetProcAddress(original, #name);
    FORWARDERS(RESOLVE_PTR)
#undef RESOLVE_PTR

    // 2) 加载补丁本体（失败不影响音频；另开线程等 gdi32 就绪后重试一次）
    {
        int n = 0, k = 0;
        static const char suffix[] = { 92,'d','a','t','a',92,'u','p','d','a','t','e',92,'c','n','p','a','t','c','h','.','d','l','l',0 };
        while (g_dir[n] && n < 460) { g_patch_path[n] = g_dir[n]; ++n; }
        while (suffix[k]) g_patch_path[n++] = suffix[k++];
        g_patch_path[n] = 0;
        log_text("cnpatch.path", g_patch_path);
        /* 不在 DllMain 里直接 LoadLibrary：进程还在加载器临界区内，实测会被拒绝
           (ERROR_INVALID_ADDRESS)。统一交给线程，等 gdi32 就绪后再加载。 */
        if (pCreateThread) pCreateThread(0, 0, retry_thread, 0, 0, 0);
        else g_patch = try_load_patch();
    }
    return 1;
}
