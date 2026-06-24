#ifndef CSQLITEVEC_H
#define CSQLITEVEC_H

#ifdef __cplusplus
extern "C" {
#endif

/// 把 sqlite-vec 注册到指定的 SQLite 连接上(包装 sqlite3_vec_init,统一 void* 入参
/// 以避开 Swift clang importer 对 sqlite3 * 的歧义)
/// 返回 SQLITE_OK (=0) 表示成功
int sqlite3_vec_register(void *db);

/// 当前 amalgamation 的版本字符串(vec0.c 编译期常量)
const char *sqlite3_vec_version_string(void);

#ifdef __cplusplus
}
#endif

#endif /* CSQLITEVEC_H */