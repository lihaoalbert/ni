#ifndef SQLITE_VEC_H
#define SQLITE_VEC_H

#include <sqlite3.h>

/* sqlite-vec v0.1.9 amalgamation — header shim
 *
 * vec0.c needs a handful of macros (`SQLITE_VEC_API` for visibility,
 * `SQLITE_VEC_VERSION*` for `vec_debug` output). The official source
 * tree's sqlite-vec.h defines these; the amalgamation tarball omits it.
 * We declare them inline here so the amalgamation compiles standalone
 * under SwiftPM's ctarget.
 *
 * Swift 端调 vec_init 用 `sqlite3_auto_extension`,签名是标准的 loadext_entry。
 */

#define SQLITE_VEC_VERSION          "v0.1.9"
#define SQLITE_VEC_VERSION_MAJOR    0
#define SQLITE_VEC_VERSION_MINOR    1
#define SQLITE_VEC_VERSION_PATCH    9
#define SQLITE_VEC_DATE             "2025-02-14"
#define SQLITE_VEC_SOURCE           "amalgamation-vendored"

#ifdef _WIN32
#  define SQLITE_VEC_API __declspec(dllexport)
#else
#  define SQLITE_VEC_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

SQLITE_VEC_API int sqlite3_vec_init(sqlite3 *db, char **pzErrMsg, const sqlite3_api_routines *pApi);

#ifdef __cplusplus
}
#endif

#endif /* SQLITE_VEC_H */