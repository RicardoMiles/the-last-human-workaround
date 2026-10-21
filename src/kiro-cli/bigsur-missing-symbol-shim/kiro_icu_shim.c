#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <stddef.h>
#include <string.h>
#include <dlfcn.h>
#include <CoreFoundation/CoreFoundation.h>

/*
 * kiro_icu_shim.c - ICU bridge for macOS 11 (Big Sur).
 *
 * Trimmed down from my own earlier OpenCode Big Sur ICU bridge (same approach,
 * verified working there). The kiro-cli-chat runtime (an embedded newer bun) imports
 * ICU symbols from /usr/lib/libicucore.A.dylib that only exist on macOS 12+:
 *
 *   _ucal_getTimeZoneOffsetFromLocal      (macOS 12+)
 *   _ubrk_clone                           (macOS 13+)
 *   _udtitvfmt_formatCalendarToResult
 *   _unumrf_*  (openForSkeletonWithCollapseAndIdentityFallback, formatDoubleRange,
 *               formatDecimalRange, openResult, closeResult, resultAsValue,
 *               close)
 *   _uplrules_selectForRange
 *
 * When launched with DYLD_FORCE_FLAT_NAMESPACE=1 and DYLD_INSERT_LIBRARIES
 * pointing at the enclosing dylib, these definitions satisfy the flat-namespace
 * bindings. Where the real semantics are cheap, we delegate to the system ICU;
 * otherwise the symbol simply must exist to keep the process loading.
 *
 * NOTE: deliberately does NOT include the sqlite3_* forwarding or the dlopen
 * redirect from the opencode shim; kiro's bun must keep whatever sqlite it
 * links, and we must not shadow it in the flat namespace.
 */

#ifdef __cplusplus
extern "C" {
#endif

static int shim_verbose(void) { static int v = -1; if (v < 0) v = getenv("SHIM_LOG") ? 1 : 0; return v; }
static void shim_log(const char *msg, const void *a) { if (shim_verbose()) fprintf(stderr, "[shim] %s %p\n", msg, a); }

/*
 * DYLD_FORCE_FLAT_NAMESPACE disables two-level binding; system frameworks
 * that reference Foundation's global NSString constants (which Foundation
 * does not export as flat-namespace symbols) then fail to load with
 * "Symbol not found: _NSGenericException". Export shims for those constants
 * (toll-free-bridged CFStrings behave identically).
 */
__attribute__((visibility("default")))
void *NSGenericException = (void *)CFSTR("NSGenericException");

typedef int32_t UErrorCode;
typedef struct UCalendar UCalendar;
typedef struct UBreakIterator UBreakIterator;
typedef struct UPluralRules UPluralRules;
typedef struct UDateIntervalFormat UDateIntervalFormat;
typedef struct UFormattedDateInterval UFormattedDateInterval;
typedef uint16_t UChar;
#define U_ZERO_ERROR 0

/* ---- macOS 12/13 ICU symbols missing from macOS 11 (flat-namespace placeholder) ---- */

static char shim_dummy[16];

void udtitvfmt_close(void *fmt) { shim_log("udtitvfmt_close", fmt); }
void udtitvfmt_closeResult(void *r) { (void)r; }
void udtitvfmt_formatToResult(void *fmt, void *from, void *to, void *r, void *status) {
  (void)fmt; (void)from; (void)to; (void)r; (void)status;
}
void *udtitvfmt_open(void) { shim_log("udtitvfmt_open", 0); return shim_dummy; }
void *udtitvfmt_openResult(void) { shim_log("udtitvfmt_openResult", 0); return shim_dummy; }
void *udtitvfmt_resultAsValue(void *r, void *status) { shim_log("udtitvfmt_resultAsValue", r); return NULL; }

void udtitvfmt_formatCalendarToResult(
    const UDateIntervalFormat *uitvfmt,
    UCalendar *from, UCalendar *to,
    UFormattedDateInterval *result, UErrorCode *status) {
  (void)uitvfmt; (void)from; (void)to; (void)result;
  shim_log("udtitvfmt_formatCalendarToResult", result);
  if (status) *status = 0;
}

void unumrf_close(void *fmt) { (void)fmt; }
void unumrf_closeResult(void *r) { (void)r; }
void unumrf_formatDecimalRange(void *fmt, void *a, void *b, void *r, void *status) {
  (void)fmt; (void)a; (void)b; (void)r; (void)status;
}
void unumrf_formatDoubleRange(void *fmt, void *a, void *b, void *r, void *status) {
  (void)fmt; (void)a; (void)b; (void)r; (void)status;
}
void *unumrf_openForSkeletonWithCollapseAndIdentityFallback(void) { shim_log("unumrf_open", 0); return shim_dummy; }
void *unumrf_openResult(void) { shim_log("unumrf_openResult", 0); return shim_dummy; }
void *unumrf_resultAsValue(void *r, void *status) { (void)r; (void)status; return NULL; }

int32_t uplrules_selectForRange(const UPluralRules *rules, double start, double limit,
                                UChar *keyword, int32_t capacity, UErrorCode *status) {
  shim_log("uplrules_selectForRange", rules);
  (void)rules; (void)start; (void)limit;
  if (status) *status = 0;
  /* English-style plural rules produce "other" for ranges unless the endpoints
   * agree on a category; approximating with "other" keeps the API contract
   * (return keyword length, write keyword buffer). */
  static const uint16_t other[] = { 'o','t','h','e','r', 0 };
  const int32_t n = 5;
  if (keyword && capacity > 0) {
    int32_t copy = capacity - 1;
    if (copy > n) copy = n;
    for (int32_t i = 0; i < copy; i++) keyword[i] = other[i];
    keyword[copy] = 0;
  }
  return n;
}

/* tz offset from local time: macOS 11 ICU (ICU 65) lacks the macOS 12 helper;
 * everything it needs already exists (ucal_getMillis / ucal_getOffset). */
void ucal_getTimeZoneOffsetFromLocal(const UCalendar *cal, int32_t isLocal,
                                     int32_t *rawOffset, int32_t *dstOffset,
                                     UErrorCode *status) {
  shim_log("ucal_getTimeZoneOffsetFromLocal", cal);
  (void)isLocal;
  static double (*real_getMillis)(const UCalendar *, UErrorCode *) = 0;
  static void (*real_getOffset)(const UCalendar *, double, int32_t,
                                int32_t *, int32_t *, UErrorCode *) = 0;
  if (!real_getMillis || !real_getOffset) {
    void *h = dlopen("/usr/lib/libicucore.A.dylib", RTLD_LAZY);
    if (h) {
      real_getMillis = (double (*)(const UCalendar *, UErrorCode *))dlsym(h, "ucal_getMillis");
      real_getOffset = (void (*)(const UCalendar *, double, int32_t,
                                 int32_t *, int32_t *, UErrorCode *))dlsym(h, "ucal_getOffset");
    }
  }
  if (!real_getMillis || !real_getOffset) {
    if (status) *status = 1;
    return;
  }
  if (status && *status > U_ZERO_ERROR) return;
  double now = real_getMillis(cal, status);
  real_getOffset(cal, now, isLocal, rawOffset, dstOffset, status);
}

UBreakIterator *ubrk_clone(const UBreakIterator *bi, UErrorCode *status) {
  shim_log("ubrk_clone", bi);
  /* macOS 11 ICU (ICU 68) lacks _ubrk_clone but keeps the older
   * _ubrk_safeClone. Delegate to it to get an independent heap clone,
   * otherwise aliasing would double-free on later ubrk_close. */
  static void *(*real_safeClone)(const void *, void *, int32_t *, UErrorCode *) = 0;
  if (!real_safeClone) {
    void *h = dlopen("/usr/lib/libicucore.A.dylib", RTLD_LAZY);
    if (h) real_safeClone = (void *(*)(const void *, void *, int32_t *, UErrorCode *))dlsym(h, "ubrk_safeClone");
    if (!real_safeClone) {
      if (status) *status = 1;
      return (UBreakIterator *)bi;
    }
  }
  int32_t need = 4096;
  for (int attempt = 0; attempt < 8; attempt++) {
    void *buf = calloc(1, (size_t)need);
    UErrorCode s1 = 0;
    void *cl = real_safeClone(bi, buf, &need, &s1);
    if (cl) {
      if (status) *status = s1;
      return (UBreakIterator *)cl;
    }
    free(buf);
    if (need <= 0) need = 4096 << (attempt + 1);
    if (need > (1 << 24)) break;
  }
  if (status) *status = 1;
  return (UBreakIterator *)bi;
}

/* ---- interpose: real ICU fns that exist on macOS 11 but whose behavior
 * ---- differs from macOS 12/13. We still delegate to the real implementation,
 * ---- only adjusting inputs. */

/* Skeleton passed by JSC: "currency/USD rounding-mode-half-up integer-width-"star-0" .00 sign-auto group-auto"
 * macOS 11 ICU (ICU 68) rejects `integer-width/"star-0"` -> returns garbage status (65811),
 * JSC treats nonzero status as "Failed to initialize NumberFormat".
 * Fix: drop `integer-width/...` tokens before calling real ICU. */
static void sanitize_skeleton(const uint16_t *skel, int32_t skelLen,
                              uint16_t *out, int32_t *outLen) {
  int32_t o = 0;
  int32_t i = 0;
  while (i < skelLen) {
    while (i < skelLen && skel[i] == 0x20) i++;
    int32_t start = i;
    while (i < skelLen && skel[i] != 0x20) i++;
    int32_t len = i - start;
    if (len > 0) {
      int drop = 0;
      /* token starts with "integer-width/" (ASCII, idx 0..13) */
      static const char kw[] = "integer-width/";
      if (len >= 14 && (skel[start] < 128) && (skel[start] & 0xFF) == 'i') {
        int k = 1;
        for (; k < 13; k++)
          if (skel[start + k] < 128 && (skel[start + k] & 0xFF) == (unsigned char)kw[k])
            continue;
          else
            break;
        if (k == 13 && skel[start + 13] == '/') drop = 1;
      }
      if (!drop) {
        if (o > 0) out[o++] = 0x20;
        for (int32_t j = 0; j < len; j++) out[o++] = skel[start + j];
      }
    }
  }
  *outLen = o;
}

static void *(*real_unumf_openForSkeletonAndLocale)(const uint16_t *s, int32_t n,
                                                    const char *loc, UErrorCode *st) = 0;

void *unumf_openForSkeletonAndLocale(const uint16_t *skel, int32_t skelLen,
                                     const char *locale, UErrorCode *status) {
  if (!real_unumf_openForSkeletonAndLocale) {
    void *h = dlopen("/usr/lib/libicucore.A.dylib", RTLD_LAZY);
    if (h) real_unumf_openForSkeletonAndLocale = (void *(*)(const uint16_t *, int32_t, const char *, UErrorCode *))dlsym(h, "unumf_openForSkeletonAndLocale");
    if (!real_unumf_openForSkeletonAndLocale) {
      if (status) *status = 1;
      return 0;
    }
  }
  UErrorCode s0 = 0;
  void *r = real_unumf_openForSkeletonAndLocale(skel, skelLen, locale, &s0);
  int bad = (s0 > 128 || s0 < -128);
  if (!bad) {
    if (status) *status = s0;
    return r;
  }
  /* nonstandard status value (macOS 11 reject marker) -> retry sanitized */
  uint16_t clean[512];
  int32_t cleanLen = 0;
  sanitize_skeleton(skel, skelLen, clean, &cleanLen);
  UErrorCode s1 = 0;
  void *r2 = real_unumf_openForSkeletonAndLocale(clean, cleanLen, locale, &s1);
  if (status) *status = s1;
  return r2;
}

#ifdef __cplusplus
}
#endif