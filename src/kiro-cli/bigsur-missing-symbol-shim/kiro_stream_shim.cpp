//
// kiro_stream_shim.cpp
//
// macOS 11 (Big Sur) libc++ keeps <fstream>/<sstream> templates header-inline
// and does NOT export their vtables/VTTs/member instantiations.  kiro-cli-chat
// was built against a much newer libc++ (SDK 26.5) which DOES export these as
// a fixed, explicit-instantiated ABI, so the binary carries non-lazy undefined
// references to symbols Big Sur does not provide.
//
// This dylib re-emits those exact symbols using the 11.3 SDK's own libc++
// headers (identical __1 stream-class ABI), so the running binary gets
// vtables/VTTs/constructors whose layout and dispatch targets are the ones
// the modern libc++ binary expects.
//
// Injection: DYLD_INSERT_LIBRARIES=<this dylib> (see the wrapper script).
// No flat namespace is required for a binary linked against these names.
//

#include <fstream>
#include <sstream>
#include <string>

// Emit concrete vtables, VTTs and out-of-line members for the exact
// char/char_traits/allocator specialisations the binary references.
template class std::basic_filebuf<char>;
template class std::basic_stringbuf<char>;
template class std::basic_ofstream<char>;
template class std::basic_stringstream<char>;
template class std::basic_istringstream<char>;
template class std::basic_ostringstream<char>;