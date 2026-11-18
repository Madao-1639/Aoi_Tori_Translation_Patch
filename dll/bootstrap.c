// bootstrap.c —— 追加到原生 mog3x32.dll 上的引导节
//
// 唯一职责：把 cnpatch/cnpatch.dll 拉进进程。中文相关的全部动作（版本校验、
// 编码通路、GDI 钩子、字体注册）都由那个 DLL 自己完成，这里不参与。
//
// 不链接 CRT、不引用导入表，所以只保留最小自举：从 PEB 找到 kernel32，
// 在它的导出表里取 LoadLibraryA / CreateThread。
//
// 用相对路径调用 LoadLibrary：DLL 搜索顺序会先查当前工作目录与已加载模块目录，
// 游戏目录即 exe 目录，所以 "cnpatch\\cnpatch.dll" 能命中。省掉了拼接绝对路径
// 所需的 GetModuleFileNameA 与宽字符比较。
//
// 不在 DllMain 里直接加载：进程还在加载器临界区，LoadLibrary 会被
// ERROR_INVALID_ADDRESS 拒绝。开线程并在其中延后加载。

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;

typedef struct { u16 length, maximum_length; u16* buffer; } UNICODE_STRING;
typedef struct { u32 length, initialized, ss_handle, in_load_order; } PEB_LDR_DATA;
typedef struct {
    u8 inherited_address_space, read_image_file_exec_options, being_debugged, bit_field;
    u32 mutant, image_base_address;
    PEB_LDR_DATA* ldr;
} PEB;

typedef void* HMODULE;
typedef void* HANDLE;

typedef HMODULE (__stdcall *FN_LoadLibraryA)(const char*);
typedef void*   (__stdcall *FN_GetProcAddress)(HMODULE, const char*);
typedef HANDLE  (__stdcall *FN_CreateThread)(void*, u32, u32 (__stdcall*)(void*), void*, u32, u32*);
typedef u32     (__stdcall *FN_Sleep)(u32);

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

/* 相对路径：游戏的 DLL 搜索顺序会命中 exe 所在目录 */
static const char kPatchPath[] = "cnpatch\\cnpatch.dll";

static u32 __stdcall bootstrap_thread(void* param)
{
    FN_LoadLibraryA load = (FN_LoadLibraryA)param;
    u32 k32 = module_base("kernel32.dll");
    FN_Sleep sleep_fn = (FN_Sleep)export_lookup(k32, "Sleep");
    int i;

    if (!load) return 0;

    /* 等 gdi32 就绪：补丁要钩 GDI，太早会拿不到它的导出 */
    for (i = 0; i < 3000; ++i) {
        if (module_base("gdi32.dll")) break;
        if (sleep_fn) sleep_fn(1);
    }
    if (sleep_fn) sleep_fn(20);

    load(kPatchPath);
    return 0;
}

/* 原生 DLL 的入口点（绝对地址，构建时由 build_loader.sh 从文件头读出并 -D 传入）。
   我们的节占据了 AddressOfEntryPoint，所以必须在做完自己的事之后把控制权
   交还给原生初始化例程——它负责 CRT 初始化、TLS，并调用原生 DllMain。
   跳过它会导致 STATUS_DLL_INIT_FAILED (0xc0000142)。 */
#ifndef ORIG_ENTRY_RVA
#error "ORIG_ENTRY_RVA 未定义：构建时需传入原生 DLL 的入口 RVA"
#endif

typedef int (__stdcall *FN_OrigEntry)(HMODULE, u32, void*);

int __stdcall DllMain(HMODULE self, u32 reason, void* reserved)
{
    /* self 即本模块基址，加上 RVA 得到原生入口的绝对地址。
       用 RVA 而不是写死绝对值，模块被重定位时仍然正确。 */
    FN_OrigEntry orig = (FN_OrigEntry)((u32)self + ORIG_ENTRY_RVA);
    int ok;

    /* 先让原生初始化跑完（CRT/TLS/全局构造 + 原生 DllMain）。
       必须先做这一步：此刻进程持有加载器锁，我们在锁内再做重量级操作
       （遍历 PEB、CreateThread）容易与加载器互相等待，表现为游戏卡住不出窗口。 */
    ok = orig(self, reason, reserved);
    if (reason != 1 /* DLL_PROCESS_ATTACH */) return ok;

    {
        u32 k32 = module_base("kernel32.dll");
        FN_LoadLibraryA load = (FN_LoadLibraryA)export_lookup(k32, "LoadLibraryA");
        FN_CreateThread create_thread =
            (FN_CreateThread)export_lookup(k32, "CreateThread");
        if (load && create_thread)
            create_thread(0, 0, bootstrap_thread, (void*)load, 0, 0);
    }
    return ok;
}
