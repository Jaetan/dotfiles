/* claude-call-rmdir: preloaded in a claude-call view, where each directory ignored whole is bound live and so is a
 * mount point. The kernel refuses to remove a mount point (EBUSY) even once it is empty, and mkdir finds it there
 * (EEXIST), so `rm -rf build && mkdir build`, Python's shutil.rmtree and GHC's removePathForcibly would fail in the
 * view where they succeed in the real tree. Here, on an empty directory that is a mount point, removing it and
 * creating it read as success; every other outcome is the real call's.
 *
 * A mount point is told by its mount id (statx STATX_MNT_ID), not by st_dev: a bind mount within one filesystem
 * keeps its parent's st_dev. Built by claude-call with `cc -shared -fPIC -O2 -o claude-call-rmdir.so <this> -ldl`.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int empty_mount_point(int dirfd, const char *path) {
    int saved = errno;
    int result = 0;
    struct statx self;
    struct statx parent;
    char up[PATH_MAX];
    if (statx(dirfd, path, AT_SYMLINK_NOFOLLOW, STATX_TYPE | STATX_MNT_ID, &self) == 0 && S_ISDIR(self.stx_mode)
        && snprintf(up, sizeof up, "%s/..", path) < (int)sizeof up
        && statx(dirfd, up, 0, STATX_MNT_ID, &parent) == 0 && parent.stx_mnt_id != self.stx_mnt_id) {
        int fd = openat(dirfd, path, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
        DIR *dir = fd >= 0 ? fdopendir(fd) : NULL;
        if (dir != NULL) {
            result = 1;
            for (const struct dirent *entry; (entry = readdir(dir)) != NULL;) {
                if (strcmp(entry->d_name, ".") != 0 && strcmp(entry->d_name, "..") != 0) {
                    result = 0;
                    break;
                }
            }
            closedir(dir);
        } else if (fd >= 0) {
            close(fd);
        }
    }
    errno = saved;
    return result;
}

int rmdir(const char *path) {
    static int (*real)(const char *);
    if (real == NULL) real = (int (*)(const char *))dlsym(RTLD_NEXT, "rmdir");
    int rc = real(path);
    return rc != 0 && errno == EBUSY && empty_mount_point(AT_FDCWD, path) ? 0 : rc;
}

int unlinkat(int dirfd, const char *path, int flags) {
    static int (*real)(int, const char *, int);
    if (real == NULL) real = (int (*)(int, const char *, int))dlsym(RTLD_NEXT, "unlinkat");
    int rc = real(dirfd, path, flags);
    return rc != 0 && errno == EBUSY && (flags & AT_REMOVEDIR) != 0 && empty_mount_point(dirfd, path) ? 0 : rc;
}

int mkdir(const char *path, mode_t mode) {
    static int (*real)(const char *, mode_t);
    if (real == NULL) real = (int (*)(const char *, mode_t))dlsym(RTLD_NEXT, "mkdir");
    int rc = real(path, mode);
    return rc != 0 && errno == EEXIST && empty_mount_point(AT_FDCWD, path) ? 0 : rc;
}

int mkdirat(int dirfd, const char *path, mode_t mode) {
    static int (*real)(int, const char *, mode_t);
    if (real == NULL) real = (int (*)(int, const char *, mode_t))dlsym(RTLD_NEXT, "mkdirat");
    int rc = real(dirfd, path, mode);
    return rc != 0 && errno == EEXIST && empty_mount_point(dirfd, path) ? 0 : rc;
}
