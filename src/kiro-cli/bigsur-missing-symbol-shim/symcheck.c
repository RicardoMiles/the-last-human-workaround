/*
 * symcheck.c - resolve kiro-cli-chat undefined symbols the way dyld does.
 *
 * Loads the same dylibs the binary links against (RTLD_GLOBAL to put them
 * into the global scope), then dlsym(RTLD_DEFAULT, ...) each symbol from stdin.
 *
 * Must be run under the wrapper's own environment:
 *   DYLD_FORCE_FLAT_NAMESPACE=1 DYLD_INSERT_LIBRARIES=<shim> ./symcheck
 * so the shim's exports participate in resolution just like at runtime.
 *
 * Prints "MISSING <sym>" for unresolvable symbols, summary at the end.
 *
 * Important: this tests flat-namespace resolution. See SYMCHECK.md pitfalls.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dlfcn.h>

static const char *libs[] = {
    "/System/Library/Frameworks/CoreFoundation.framework/Versions/A/CoreFoundation",
    "/System/Library/Frameworks/AppKit.framework/Versions/C/AppKit",
    "/System/Library/Frameworks/SystemConfiguration.framework/Versions/A/SystemConfiguration",
    "/usr/lib/libSystem.B.dylib",
    "/System/Library/Frameworks/Foundation.framework/Versions/C/Foundation",
    "/System/Library/Frameworks/CoreGraphics.framework/Versions/A/CoreGraphics",
    "/usr/lib/libobjc.A.dylib",
    "/System/Library/Frameworks/IOKit.framework/Versions/A/IOKit",
    "/usr/lib/libc++.1.dylib",
    "/System/Library/Frameworks/Accelerate.framework/Versions/A/Accelerate",
    "/System/Library/Frameworks/AudioUnit.framework/Versions/A/AudioUnit",
    "/System/Library/Frameworks/CoreAudio.framework/Versions/A/CoreAudio",
    "/System/Library/Frameworks/CoreServices.framework/Versions/A/CoreServices",
    "/System/Library/Frameworks/Security.framework/Versions/A/Security",
    "/usr/lib/libiconv.2.dylib",
    "/System/Library/Frameworks/CoreML.framework/Versions/A/CoreML",
    /* embedded runtime also uses ICU directly */
    "/usr/lib/libicucore.A.dylib",
    NULL
};

int main(void) {
    int loaded = 0, failed_libs = 0;
    for (int i = 0; libs[i]; i++) {
        if (dlopen(libs[i], RTLD_NOW | RTLD_GLOBAL)) {
            loaded++;
        } else {
            failed_libs++;
            fprintf(stderr, "LIBFAIL %s: %s\n", libs[i], dlerror());
        }
    }
    fprintf(stderr, "loaded %d libs (%d failed)\n", loaded, failed_libs);

    char line[1024];
    int total = 0, missing = 0;
    while (fgets(line, sizeof line, stdin)) {
        char *s = line;
        while (*s == ' ' || *s == '\t') s++;
        size_t n = strlen(s);
        while (n && (s[n-1] == '\n' || s[n-1] == '\r' || s[n-1] == ' ')) s[--n] = 0;
        if (!n) continue;
        total++;
        /* dlsym names don't include Mach-O's leading underscore */
        const char *q = (s[0] == '_') ? s + 1 : s;
        dlerror();
        void *p = dlsym(RTLD_DEFAULT, q);
        if (!p) {
            missing++;
            printf("MISSING %s\n", s);
        }
    }
    printf("---\nchecked %d symbols, %d unresolved\n", total, missing);
    return 0;
}
