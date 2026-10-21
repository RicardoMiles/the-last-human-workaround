// std::__1::bad_function_call vtable/typeinfo/dtor shim - macOS 11 Big Sur.
//
// The key line is the #define below. Newer libc++ builds with the
// "key function" ABI for bad_function_call, which makes the virtual
// destructor an out-of-line key function and therefore EMITS the vtable,
// the typeinfo and the destructor as real exported symbols. Big Sur's libc++
// predates that ABI and does not define the macro, so it never emits those
// three symbols - which is exactly what the bundled libllama.dylib is bound to.
//
// Defining the macro ourselves and including <functional> makes this
// translation unit emit the three missing symbols. Inheritance chain, name
// mangling and vtable layout all come from one set of libc++ headers, so the
// layout matches what libllama expects. This is not a name-mangling
// difference - the symbols genuinely do not exist on Big Sur.
#define _LIBCPP_ABI_BAD_FUNCTION_CALL_KEY_FUNCTION 1
#include <functional>
#include <fstream>
#include <iostream>
#include <sstream>
#include <streambuf>

namespace std {
inline namespace __1 {
  bad_function_call::~bad_function_call() _NOEXCEPT {}
  const char* bad_function_call::what() const _NOEXCEPT { return "std::bad_function_call"; }
}
}

// Apple's Big Sur libc++ does not export these stream instantiations, while the
// newer libc++ the bundled libs were built against expects them from libc++.
// Explicit instantiation emits them with the ABI-compatible Big Sur layout.
template class std::basic_streambuf<char, std::char_traits<char> >;
template class std::basic_filebuf<char, std::char_traits<char> >;
template class std::basic_stringbuf<char, std::char_traits<char>, std::allocator<char> >;
template class std::basic_ios<char, std::char_traits<char> >;
template class std::basic_iostream<char, std::char_traits<char> >;
template class std::basic_ifstream<char, std::char_traits<char> >;
template class std::basic_ofstream<char, std::char_traits<char> >;
template class std::basic_fstream<char, std::char_traits<char> >;
template class std::basic_istringstream<char, std::char_traits<char>, std::allocator<char> >;
template class std::basic_ostringstream<char, std::char_traits<char>, std::allocator<char> >;
template class std::basic_stringstream<char, std::char_traits<char>, std::allocator<char> >;
