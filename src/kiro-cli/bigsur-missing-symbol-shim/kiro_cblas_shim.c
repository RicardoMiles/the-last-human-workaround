//
// cblas ILP64 forwarding stub.
//
// kiro-cli-chat is bound to _cblas_sgemm$NEWLAPACK$ILP64 (a symbol that only
// newer Accelerate exports).  Big Sur exports the LP64 cblas_sgemm, which on
// x86_64 differs only in the width of the integer dims (int vs long).  Since
// every arg already travels in an 8-byte general-purpose slot, a wrapper that
// truncates the ILP64 longs to ints is a faithful LP64 delegate.
//

#include <stdint.h>

#if __LP64__
#define ILP64_INT long
#else
#error "shim is only meaningful on LP64"
#endif

extern void cblas_sgemm_impl(
    int layout, int transa, int transb, int M, int N, int K, float alpha,
    const float *A, int lda, const float *B, int ldb, float beta,
    float *C, int ldc) __asm__("_cblas_sgemm");

void cblas_sgemm_ilp64_shim(
    int layout, int transa, int transb, ILP64_INT M, ILP64_INT N, ILP64_INT K,
    float alpha, const float *A, ILP64_INT lda, const float *B, ILP64_INT ldb,
    float beta, float *C, ILP64_INT ldc)
__asm__("_cblas_sgemm$NEWLAPACK$ILP64");

void cblas_sgemm_ilp64_shim(
    int layout, int transa, int transb, ILP64_INT M, ILP64_INT N, ILP64_INT K,
    float alpha, const float *A, ILP64_INT lda, const float *B, ILP64_INT ldb,
    float beta, float *C, ILP64_INT ldc)
{
    cblas_sgemm_impl(layout, transa, transb,
                     (int)M, (int)N, (int)K, alpha, A,
                     (int)lda, B, (int)ldb, beta, C, (int)ldc);
}