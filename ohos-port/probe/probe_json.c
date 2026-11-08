/* probe_json.c — ProbeReport → JSON（纯逻辑，无平台依赖，PC 可单测）
 *
 * 输出结构（键名即 TC 判定契约）：
 * {
 *   "meta": {"started_at":..,"finished_at":..,"device_model":..,"os_version":..},
 *   "vk":   {"has_vk_ohos_surface":..,"api_version":"x.y.z","api_version_raw":N,
 *            "device_name":..,"driver_info":..,"present_ms":N},
 *   "bcn":  {"VK_FORMAT_...":bool ×16}, "bcn_all":bool,
 *   "astc": {"4x4":bool,"8x8":bool},
 *   "mem":  {"reserve_5g":..,"reserve_headroom_8g":..,
 *            "headroom_contiguous":..,"commit_2g_ok":..},
 *   "regs": {"tpidr_rw":..,"tpidrro_readable":..,"tpidrro_value_note":..,
 *            "cntpct_ok":..,"cntfrq_hz":N,"tls_per_thread":..,
 *            "futex_ok":..,"wx_ok":..,"rewritten_nso_runs":..},
 *   "checks": {"<item>": {"ok":..,"crashed":..,"sig":N,"note":..} × N}
 * }
 */
#include "probe_report.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* VK 版本字段提取（避免依赖 vulkan.h 的宏，PC 单测亦可验） */
#define VK_VER_MAJOR(v) (((uint32_t)(v) >> 22) & 0x3FFU)
#define VK_VER_MINOR(v) (((uint32_t)(v) >> 12) & 0x3FFU)
#define VK_VER_PATCH(v) ((uint32_t)(v) & 0xFFFU)

/* BCn 16 键 —— 与 probe_vk.c 的 BCN_FORMATS[] 同源展开（bcn_formats.def），
 * 结构上保证键名与探测格式顺序一致（JSON 键名即 TC0-3-1 判定契约） */
const char *const probe_bcn_keys[PROBE_BCn_COUNT] = {
#define BCN_ENTRY(fmt) #fmt,
#include "bcn_formats.def"
#undef BCN_ENTRY
};
_Static_assert(sizeof(probe_bcn_keys) / sizeof(probe_bcn_keys[0]) == PROBE_BCn_COUNT,
               "bcn_formats.def 项数必须等于 PROBE_BCn_COUNT");

/* ---------- 动态缓冲 ---------- */
typedef struct {
    char  *p;
    size_t len, cap;
    int    oom;   /* 粘滞 OOM：一旦失败，后续写入全部丢弃（防输出半成品 JSON） */
} Buf;

static void buf_grow(Buf *b, size_t need)
{
    if (b->oom)
        return;
    if (!b->p || b->len + need > b->cap) {
        size_t ncap = b->cap ? b->cap * 2 : 4096;
        while (ncap < b->len + need)
            ncap *= 2;
        char *np = (char *)realloc(b->p, ncap);
        if (!np) {                  /* OOM：丢内容不崩探针，json 返回 NULL */
            b->oom = 1;
            free(b->p);
            b->p = NULL;
            b->len = b->cap = 0;
            return;
        }
        b->p = np;
        b->cap = ncap;
    }
}

static void buf_putc(Buf *b, char c)
{
    buf_grow(b, 1);
    if (!b->p) return;
    b->p[b->len++] = c;
    b->p[b->len] = '\0';
}

static void buf_putn(Buf *b, const char *s, size_t n)
{
    buf_grow(b, n + 1);
    if (!b->p) return;
    memcpy(b->p + b->len, s, n);
    b->len += n;
    b->p[b->len] = '\0';
}

static void buf_puts(Buf *b, const char *s)
{
    buf_putn(b, s, strlen(s));
}

/* ---------- JSON 转义（PC 单测覆盖 " \ \n \r \t \b \f 控制字符、UTF-8 直通）
 * 返回所需字符数（不含 '\0'）；dst==NULL 或容量不足时仅计算不写入。
 * 截断策略：独立写游标 wp，可写容量 = dst_cap-1；一个"段"（普通字符 1 字节、
 * 转义序列 2-6 字节）放不下则整段跳过不进位，保证已写前缀总是合法 JSON、
 * 不出现半截转义。 */
int probe_json_escape(const char *src, char *dst, size_t dst_cap)
{
    size_t need = 0;                    /* 所需总长（与写入无关，恒累计） */
    size_t wp   = 0;                    /* 写游标（≤ dst_cap-1） */
    const size_t wr_cap = (dst && dst_cap) ? dst_cap - 1 : 0;
    for (const unsigned char *s = (const unsigned char *)src; *s; s++) {
        char        seg[8];
        const char *esc = NULL;
        switch (*s) {
        case '"':  esc = "\\\""; break;
        case '\\': esc = "\\\\"; break;
        case '\n': esc = "\\n";  break;
        case '\r': esc = "\\r";  break;
        case '\t': esc = "\\t";  break;
        case '\b': esc = "\\b";  break;
        case '\f': esc = "\\f";  break;
        default:
            if (*s < 0x20) {
                snprintf(seg, sizeof seg, "\\u%04x", (unsigned)*s);
                esc = seg;
            } else {
                seg[0] = (char)*s;
                seg[1] = '\0';
                esc = seg;
            }
            break;
        }
        size_t n = strlen(esc);
        if (dst && wp + n <= wr_cap) {   /* 整段放得下才写入（防截断空洞） */
            memcpy(dst + wp, esc, n);
            wp += n;
        }
        need += n;
    }
    if (dst && dst_cap)
        dst[wp] = '\0';                  /* wp ≤ cap-1，NUL 必有位置 */
    return (int)need;
}

static void put_json_str(Buf *b, const char *s)
{
    char stackbuf[512];
    const char *src = s ? s : "";
    int need = probe_json_escape(src, stackbuf, sizeof stackbuf);
    buf_putc(b, '"');
    if (need >= 0 && (size_t)need < sizeof stackbuf) {
        buf_putn(b, stackbuf, (size_t)need);
    } else {                       /* 超长走堆 */
        char *heap = (char *)malloc((size_t)need + 1);
        if (heap) {
            probe_json_escape(src, heap, (size_t)need + 1);
            buf_putn(b, heap, (size_t)need);
            free(heap);
        }
    }
    buf_putc(b, '"');
}

static void put_item(Buf *b, const char *key, const ProbeItem *it)
{
    char sig[16];
    snprintf(sig, sizeof sig, "%d", it->sig);
    buf_putc(b, '"');
    buf_puts(b, key);
    buf_puts(b, "\":{\"ok\":");
    buf_puts(b, it->ok ? "true" : "false");
    buf_puts(b, ",\"crashed\":");
    buf_puts(b, it->crashed ? "true" : "false");
    buf_puts(b, ",\"sig\":");
    buf_puts(b, sig);
    buf_puts(b, ",\"note\":");
    put_json_str(b, it->note);
    buf_putc(b, '}');
}

/* ---------- 纯逻辑助手 ---------- */
bool probe_bcn_all(const bool *bcn)
{
    for (int i = 0; i < PROBE_BCn_COUNT; i++)
        if (!bcn[i])
            return false;
    return true;
}

/* M0.5a：哨兵 = t0 ^ 0x5555...（与 B 文档伪代码一致）。
 * 掩码非零 ⇒ SENTINEL 恒 ≠ t0（防恒真），且 SENTINEL==0 仅当 t0==掩码本身。 */
uint64_t probe_sentinel(uint64_t t0)
{
    return t0 ^ 0x5555555555555555ULL;
}

bool probe_tpidr_writable_check(uint64_t t0, uint64_t t1)
{
    return t1 == probe_sentinel(t0);
}

/* M0.5c：按实读 cntfrq_el0 换算期望 tick 数再作 10% 容差判定。
 * 全程 uint64 运算；|b-a - expect| 用差值分支避免有符号回绕。 */
bool probe_cntpct_judge(uint64_t freq, uint64_t a, uint64_t b, uint64_t elapsed_ms)
{
    if (freq == 0)
        return false;
    if (b <= a)
        return false;                       /* 单调性是硬前提 */
    uint64_t delta  = b - a;
    uint64_t expect = freq * elapsed_ms / 1000;
    uint64_t slack  = expect / 10;
    uint64_t err    = delta > expect ? delta - expect : expect - delta;
    return err <= slack;
}

/* ---------- 顶层序列化 ---------- */
char *probe_report_json(const ProbeReport *r, size_t *out_len)
{
    Buf b = {0};

    buf_puts(&b, "{\"meta\":{");
    buf_puts(&b, "\"started_at\":");
    put_json_str(&b, r->started_at);
    buf_puts(&b, ",\"finished_at\":");
    put_json_str(&b, r->finished_at);
    buf_puts(&b, ",\"device_model\":");
    put_json_str(&b, r->device_model);
    buf_puts(&b, ",\"os_version\":");
    put_json_str(&b, r->os_version);
    buf_puts(&b, ",\"build\":");
    put_json_str(&b, r->build);
    buf_putc(&b, '}');

    /* vk */
    char ver[64], raw[16], pms[32];
    snprintf(ver, sizeof ver, "%u.%u.%u",
             (unsigned)VK_VER_MAJOR(r->vk_api_version),
             (unsigned)VK_VER_MINOR(r->vk_api_version),
             (unsigned)VK_VER_PATCH(r->vk_api_version));
    snprintf(raw, sizeof raw, "%u", (unsigned)r->vk_api_version);
    snprintf(pms, sizeof pms, "%.3f", r->present_ms);
    buf_puts(&b, ",\"vk\":{\"has_vk_ohos_surface\":");
    buf_puts(&b, r->vk_surface.ok ? "true" : "false");
    buf_puts(&b, ",\"api_version\":");
    put_json_str(&b, ver);
    buf_puts(&b, ",\"api_version_raw\":");
    buf_puts(&b, raw);
    buf_puts(&b, ",\"device_name\":");
    put_json_str(&b, r->vk_device_name);
    buf_puts(&b, ",\"driver_info\":");
    put_json_str(&b, r->vk_driver_info);
    buf_puts(&b, ",\"present_ms\":");
    buf_puts(&b, pms);
    buf_putc(&b, '}');

    /* bcn ×16 */
    buf_puts(&b, ",\"bcn\":{");
    for (int i = 0; i < PROBE_BCn_COUNT; i++) {
        if (i) buf_putc(&b, ',');
        buf_putc(&b, '"');
        buf_puts(&b, probe_bcn_keys[i]);
        buf_puts(&b, "\":");
        buf_puts(&b, r->bcn[i] ? "true" : "false");
    }
    buf_puts(&b, "},\"bcn_all\":");
    buf_puts(&b, r->bcn_all ? "true" : "false");

    buf_puts(&b, ",\"astc\":{\"4x4\":");
    buf_puts(&b, r->astc_4x4 ? "true" : "false");
    buf_puts(&b, ",\"8x8\":");
    buf_puts(&b, r->astc_8x8 ? "true" : "false");
    buf_putc(&b, '}');

    /* mem */
    buf_puts(&b, ",\"mem\":{\"reserve_5g\":");
    buf_puts(&b, r->mem_reserve_5g.ok ? "true" : "false");
    buf_puts(&b, ",\"reserve_headroom_8g\":");
    buf_puts(&b, r->mem_headroom_8g.ok ? "true" : "false");
    buf_puts(&b, ",\"headroom_contiguous\":");
    buf_puts(&b, r->headroom_contiguous ? "true" : "false");
    buf_puts(&b, ",\"commit_2g_ok\":");
    buf_puts(&b, r->mem_commit_2g.ok ? "true" : "false");
    buf_putc(&b, '}');

    /* regs */
    char hz[32];
    snprintf(hz, sizeof hz, "%llu", (unsigned long long)r->cntfrq_hz);
    buf_puts(&b, ",\"regs\":{\"tpidr_rw\":");
    buf_puts(&b, r->tpidr_rw.ok ? "true" : "false");
    buf_puts(&b, ",\"tpidrro_readable\":");
    buf_puts(&b, r->tpidrro.ok ? "true" : "false");
    buf_puts(&b, ",\"tpidrro_value_note\":");
    put_json_str(&b, r->tpidrro.note);
    buf_puts(&b, ",\"cntpct_ok\":");
    buf_puts(&b, r->cntpct.ok ? "true" : "false");
    buf_puts(&b, ",\"cntfrq_hz\":");
    buf_puts(&b, hz);
    buf_puts(&b, ",\"tls_per_thread\":");
    buf_puts(&b, r->tls_per_thread.ok ? "true" : "false");
    buf_puts(&b, ",\"futex_ok\":");
    buf_puts(&b, r->futex.ok ? "true" : "false");
    buf_puts(&b, ",\"wx_ok\":");
    buf_puts(&b, r->wx.ok ? "true" : "false");
    buf_puts(&b, ",\"rewritten_nso_runs\":");
    buf_puts(&b, r->mini_nso.ok ? "true" : "false");
    buf_puts(&b, ",\"aot_nso_runs\":");
    buf_puts(&b, r->aot_nso.ok ? "true" : "false");
    buf_putc(&b, '}');

    /* checks 明细（crashed 归因用） */
    buf_puts(&b, ",\"checks\":{");
    put_item(&b, "vk_surface", &r->vk_surface);           buf_putc(&b, ',');
    put_item(&b, "mem_reserve_5g", &r->mem_reserve_5g);   buf_putc(&b, ',');
    put_item(&b, "mem_headroom_8g", &r->mem_headroom_8g); buf_putc(&b, ',');
    put_item(&b, "mem_commit_2g", &r->mem_commit_2g);     buf_putc(&b, ',');
    put_item(&b, "tpidr_rw", &r->tpidr_rw);               buf_putc(&b, ',');
    put_item(&b, "tpidrro", &r->tpidrro);                 buf_putc(&b, ',');
    put_item(&b, "cntpct", &r->cntpct);                   buf_putc(&b, ',');
    put_item(&b, "tls_per_thread", &r->tls_per_thread);   buf_putc(&b, ',');
    put_item(&b, "futex", &r->futex);                     buf_putc(&b, ',');
    put_item(&b, "wx", &r->wx);                           buf_putc(&b, ',');
    put_item(&b, "mini_nso", &r->mini_nso);               buf_putc(&b, ',');
    put_item(&b, "aot_nso", &r->aot_nso);
    buf_puts(&b, "}}");

    if (b.oom) {                       /* 粘滞 OOM：宁缺毋滥，不落半成品 JSON */
        free(b.p);
        if (out_len)
            *out_len = 0;
        return NULL;
    }
    if (out_len)
        *out_len = b.len;
    return b.p;
}

int probe_report_write_str(const char *path, const char *js, size_t len)
{
    FILE *f = fopen(path, "wb");
    if (!f)
        return -1;
    int ok = fwrite(js, 1, len, f) == len;
    ok = (fclose(f) == 0) && ok;
    return ok ? 0 : -1;
}

int probe_report_write_json(const ProbeReport *r, const char *path)
{
    size_t n  = 0;
    char  *js = probe_report_json(r, &n);
    if (!js)
        return -1;
    int rc = probe_report_write_str(path, js, n);
    free(js);
    return rc;
}

void probe_report_init(ProbeReport *r)
{
    memset(r, 0, sizeof *r);
}
