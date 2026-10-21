// cblas_sgemm$NEWLAPACK$ILP64 -> plain LP64 cblas_sgemm forwarding - macOS 11.
//
// On x86_64, ILP64 and LP64 cblas_sgemm differ only in the width of the integer
// dimension arguments (long vs int). Every argument already travels in an
// 8-byte general-purpose register slot, and embedding dimensions are orders of
// magnitude below INT_MAX, so the truncation is lossless here. The real
// cblas_sgemm is resolved at runtime via dlsym (on Big Sur it lives in the
// dyld shared cache, so RTLD_DEFAULT normally finds it without a dlopen).
//
// KNOWN SILENT-FAILURE PATH: if resolution fails, the call below returns
// without computing anything. sgemm becomes a no-op, no error is raised, and
// embeddings come out silently wrong rather than crashing. Verifying that the
// shim EXPORTS the symbol does not cover this - that only proves the forwarder
// exists, not that its target resolved. The cheap real check is to compute one
// embedding and assert the result is neither all-zero nor constant.
#include <dlfcn.h>

typedef void (*lp64_sgemm_t)(
    int layout, int transa, int transb, int M, int N, int K,
    float alpha, const float *A, int lda, const float *B, int ldb,
    float beta, float *C, int ldc);

static lp64_sgemm_t g_real = 0;
static lp64_sgemm_t resolve_real(void) {
    if (g_real) return g_real;
    void *s = dlsym(RTLD_DEFAULT, "cblas_sgemm");
    if (!s) {
        void *h = dlopen("/System/Library/Frameworks/Accelerate.framework/Versions/A/Accelerate",
                         RTLD_LAZY | RTLD_GLOBAL);
        if (h) s = dlsym(h, "cblas_sgemm");
    }
    g_real = (lp64_sgemm_t)s;
    return g_real;
}

void cblas_sgemm_ilp64_shim(
    int layout, int transa, int transb, long M, long N, long K,
    float alpha, const float *A, long lda, const float *B, long ldb,
    float beta, float *C, long ldc) __asm__("_cblas_sgemm$NEWLAPACK$ILP64");

void cblas_sgemm_ilp64_shim(
    int layout, int transa, int transb, long M, long N, long K,
    float alpha, const float *A, long lda, const float *B, long ldb,
    float beta, float *C, long ldc)
{
    lp64_sgemm_t real = resolve_real();
    if (!real) return;   /* see KNOWN SILENT-FAILURE PATH above */
    real(layout, transa, transb, (int)M, (int)N, (int)K, alpha, A,
         (int)lda, B, (int)ldb, beta, C, (int)ldc);
}
