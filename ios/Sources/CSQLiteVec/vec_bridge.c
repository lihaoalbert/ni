// vec_bridge.c — Swift ↔ sqlite-vec 的桥接层
//
// 为什么需要:
// - sqlite3_vec_init 的签名是标准的 extension entry point
//   int (sqlite3*, char**, const sqlite3_api_routines*)
// - SQLite.swift 暴露的 connection.handle 是 OpaquePointer,Swift clang importer
//   把 sqlite3 * 映射成不同类型,直接调会卡在类型不匹配
// - sqlite3_auto_extension 期望 void(*)(void) — Swift @convention(c) 也强制匹配,
//   不能用 vec_init 直接注册
//
// 解法:提供一个 C 包装 sqlite3_vec_register(sqlite3 *db),签名跟 Swift 端期望的
// void * 完美对齐(从 Swift 看就是 UnsafeMutableRawPointer?)。内部调 vec_init。

#include "sqlite-vec.h"
#include <sqlite3.h>
#include <stddef.h>  // NULL

#ifdef __cplusplus
extern "C" {
#endif

int sqlite3_vec_register(void *db) {
    if (!db) {
        return SQLITE_MISUSE;
    }
    // pzErrMsg / pApi 都传 NULL — SQLite 内部用 default api routines
    return sqlite3_vec_init((sqlite3 *)db, NULL, NULL);
}

const char *sqlite3_vec_version_string(void) {
    return SQLITE_VEC_VERSION;
}

#ifdef __cplusplus
}
#endif