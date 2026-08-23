/* Inkwell.app Mach-O stub: exec the venv Python as -m inkwell.
   Resolves repo root as ../../../ from Contents/MacOS/Inkwell. */
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int repo_root(char *out, size_t n) {
    char path[PATH_MAX];
    uint32_t size = sizeof(path);
    if (_NSGetExecutablePath(path, &size) != 0) {
        return -1;
    }
    char real[PATH_MAX];
    if (!realpath(path, real)) {
        return -1;
    }
    /* $ROOT/Inkwell.app/Contents/MacOS/Inkwell → strip 4 components */
    char *p = real;
    for (int i = 0; i < 4; i++) {
        char *slash = strrchr(p, '/');
        if (!slash || slash == p) {
            return -1;
        }
        *slash = '\0';
    }
    if (strlen(p) + 1 > n) {
        return -1;
    }
    memcpy(out, p, strlen(p) + 1);
    return 0;
}

int main(int argc, char **argv) {
    char root[PATH_MAX];
    if (repo_root(root, sizeof(root)) != 0) {
        fprintf(stderr, "Inkwell: cannot resolve install root\n");
        return 1;
    }
    if (chdir(root) != 0) {
        perror("chdir");
        return 1;
    }
    if (setenv("PYTHONPATH", root, 1) != 0) {
        perror("setenv");
        return 1;
    }
    /* Clash mixed-port 403s loopback; keep the local page server off the proxy. */
    setenv("NO_PROXY", "127.0.0.1,localhost,::1", 0);
    setenv("no_proxy", "127.0.0.1,localhost,::1", 0);

    char py[PATH_MAX];
    if (snprintf(py, sizeof(py), "%s/.venv/bin/python", root) >= (int)sizeof(py)) {
        return 1;
    }

    char **newargv = calloc((size_t)argc + 3, sizeof(char *));
    if (!newargv) {
        return 1;
    }
    newargv[0] = py;
    newargv[1] = "-m";
    newargv[2] = "inkwell";
    for (int i = 1; i < argc; i++) {
        newargv[i + 2] = argv[i];
    }
    execv(py, newargv);
    perror(py);
    return 127;
}
