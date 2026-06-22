#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MegaShell - a single-file, do-everything command-line app.

Features:
  - custom command interpreter (history, tab-autocomplete, aliases, variables)
  - custom scripting language "MSL" (.msl files - if/while/for/func/print/var...)
  - REAL package manager front-end (pkg ...) -> auto-detects winget / apt / dnf /
    yum / pacman / zypper / snap / brew depending on the OS, just like winget but
    cross-platform and called "pkg"
  - PATH fallback: any command that isn't a built-in is run as a real system
    command if found on PATH (pip, pyinstaller, git, node, docker, etc. all work)
  - networking tools (ping, download, upload, http get/post, ftp, ssh, portscan...)
  - filesystem tools (copy, move, delete, compress, extract, search, watch, diff...)
  - system management (process list/kill, service start/stop, driver list, sysinfo...)
  - text & dev utilities (json format, base64, uuid, regex test, word count...)
  - Python project tooling (venv, pip wrappers, PyInstaller build wrapper)
  - Git shortcuts (gstatus, gcommit, gpush, gpull, glog, ...)
  - tiny key-value store and a persistent to-do list / notes log
  - fun / misc utilities (calc, password generator, ascii banner, timer...)

Run:
    python3 megashell.py            # interactive REPL
    python3 megashell.py file.msh   # run a command script line by line
    python3 megashell.py file.msl   # run an MSL program
"""

import os
import sys
import shutil
import subprocess
import platform
import socket
import time
import json
import re
import getpass
import zipfile
import tarfile
import hashlib
import base64
import uuid
import random
import string
import textwrap
import shlex
import filecmp
import urllib.request
import urllib.error
from datetime import datetime

try:
    import readline  # Linux/Mac - history + tab completion
    HAS_READLINE = True
except ImportError:
    HAS_READLINE = False

# ------------------------------------------------------------
# Prevent "UnicodeEncodeError: 'charmap' codec can't encode..."
# This happens on Windows when the console is using a legacy code
# page (cp1252 / cp437) instead of UTF-8. We try to force UTF-8 on
# stdout/stderr, and if that's not possible, we fall back to a
# safe-print wrapper that strips characters the console can't show
# instead of crashing.
# ------------------------------------------------------------

def _force_utf8_console():
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


_force_utf8_console()

_real_print = print  # capture the builtin BEFORE we shadow it below


def safe_print(*args, **kwargs):
    """A print() that never raises UnicodeEncodeError, even on a
    misconfigured Windows console. Falls back to ASCII with
    replacement characters if direct printing fails."""
    try:
        _real_print(*args, **kwargs)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        safe_args = []
        for a in args:
            text = a if isinstance(a, str) else str(a)
            safe_args.append(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))
        try:
            _real_print(*safe_args, **kwargs)
        except Exception:
            # last resort: strip to plain ASCII
            ascii_args = [str(a).encode("ascii", errors="replace").decode("ascii") for a in args]
            _real_print(*ascii_args, **kwargs)


# From here on, "print" inside this module refers to safe_print, so any
# command output (including third-party data, emoji, accented names, etc.)
# can never crash the shell with an encoding error.
print = safe_print

# ============================================================
#  GLOBAL STATE
# ============================================================

VERSION = "Latest"

STATE = {
    "cwd": os.getcwd(),
    "aliases": {},
    "vars": {},
    "history": [],
    "history_file": os.path.expanduser("~/.megashell_history"),
    "config_file": os.path.expanduser("~/.megashell_config.json"),
    "services": {},
    "start_time": time.time(),
    "exit_flag": False,
}

COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "white": "\033[37m",
}


def supports_color():
    return sys.stdout.isatty()


def c(text, color):
    if not supports_color():
        return text
    return f"{COLORS.get(color, '')}{text}{COLORS['reset']}"


def banner():
    print(c(r"""
 __  __                  _____ _          _ _
|  \/  | ___  __ _  __ _/  ___| |__   ___| | |
| |\/| |/ _ \/ _` |/ _` \ `--.| '_ \ / _ \ | |
| |  | |  __/ (_| | (_| |`--. \ | | |  __/ | |
|_|  |_|\___|\__, |\__,_/\__/_/_| |_|\___|_|_|
             |___/
""", "cyan"))
    print(c(f"  MegaShell v{VERSION} - a do-everything command-line app", "yellow"))
    print(c("  Type: help        - list all commands", "dim"))
    print(c("  Type: help <cmd>  - details about one command", "dim"))
    print(c("  Type: exit        - quit\n", "dim"))


def err(msg):
    print(c(f"error: {msg}", "red"))


def ok(msg):
    print(c(msg, "green"))


def info(msg):
    print(c(msg, "cyan"))


def warn(msg):
    print(c(f"warning: {msg}", "yellow"))





# ============================================================
#  CONFIG / HISTORY PERSISTENCE
# ============================================================

def load_config():
    if os.path.exists(STATE["config_file"]):
        try:
            with open(STATE["config_file"], "r", encoding="utf-8") as f:
                data = json.load(f)
                STATE["aliases"] = data.get("aliases", {})
                STATE["vars"] = data.get("vars", {})
        except Exception:
            pass
    if HAS_READLINE and os.path.exists(STATE["history_file"]):
        try:
            readline.read_history_file(STATE["history_file"])
        except Exception:
            pass


def save_config():
    try:
        with open(STATE["config_file"], "w", encoding="utf-8") as f:
            json.dump({"aliases": STATE["aliases"], "vars": STATE["vars"]}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    if HAS_READLINE:
        try:
            readline.write_history_file(STATE["history_file"])
        except Exception:
            pass


# ============================================================
#  COMMAND REGISTRY
# ============================================================

COMMANDS = {}
CATEGORIES_ORDER = []


def register(name, category, desc):
    """Decorator: registers a command into the global COMMANDS table."""
    def wrapper(fn):
        COMMANDS[name] = {"fn": fn, "desc": desc, "category": category}
        if category not in CATEGORIES_ORDER:
            CATEGORIES_ORDER.append(category)
        return fn
    return wrapper


def expand_vars(text):
    """Replace $VAR / ${VAR} with user-defined shell variables or env vars."""
    def repl(m):
        name = m.group(1) or m.group(2)
        if name in STATE["vars"]:
            return STATE["vars"][name]
        return os.environ.get(name, m.group(0))
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)", repl, text)


# ============================================================
#  1. BASIC SHELL
# ============================================================

@register("help", "Basic Shell", "List all commands (help <command> for details)")
def cmd_help(args):
    if args:
        name = args[0]
        if name in COMMANDS:
            print(c(f"\n{name}", "bold") + " - " + COMMANDS[name]["desc"])
            print(c(f"category: {COMMANDS[name]['category']}\n", "dim"))
        else:
            err(f"unknown command: {name}")
        return
    print(c("\nAvailable commands by category:", "bold"))
    for cat in CATEGORIES_ORDER:
        print(c(f"\n=== {cat} ===", "yellow"))
        names = sorted([n for n, v in COMMANDS.items() if v["category"] == cat])
        for n in names:
            print(f"  {c(n.ljust(16), 'green')} {COMMANDS[n]['desc']}")
    print(c(f"\n{len(COMMANDS)} commands total. Type: help <command> for details.\n", "dim"))


@register("cd", "Basic Shell", "Change directory: cd <path>")
def cmd_cd(args):
    target = args[0] if args else os.path.expanduser("~")
    target = os.path.expanduser(expand_vars(target))
    try:
        os.chdir(target)
        STATE["cwd"] = os.getcwd()
    except Exception as e:
        err(str(e))


@register("pwd", "Basic Shell", "Print current working directory")
def cmd_pwd(args):
    print(os.getcwd())


@register("ls", "Basic Shell", "List directory contents: ls [-l] [path]")
def cmd_ls(args):
    long_fmt = "-l" in args
    paths = [a for a in args if a != "-l"]
    target = paths[0] if paths else "."
    target = os.path.expanduser(expand_vars(target))
    try:
        entries = sorted(os.listdir(target))
    except Exception as e:
        err(str(e))
        return
    if not long_fmt:
        for e in entries:
            full = os.path.join(target, e)
            print(c(e + "/", "blue") if os.path.isdir(full) else e)
        return
    for e in entries:
        full = os.path.join(target, e)
        try:
            st = os.stat(full)
            mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            kind = "d" if os.path.isdir(full) else "-"
            print(f"{kind} {st.st_size:>10} {mtime}  {e}")
        except Exception:
            print(e)


@register("clear", "Basic Shell", "Clear the screen")
def cmd_clear(args):
    os.system("cls" if platform.system() == "Windows" else "clear")


@register("echo", "Basic Shell", "Print text: echo <text>")
def cmd_echo(args):
    print(expand_vars(" ".join(args)))


@register("history", "Basic Shell", "Show command history")
def cmd_history(args):
    for i, h in enumerate(STATE["history"][-50:], 1):
        print(f"{c(str(i).rjust(4), 'dim')}  {h}")


@register("alias", "Basic Shell", 'Create/list aliases: alias ll="ls -l"')
def cmd_alias(args):
    if not args:
        if not STATE["aliases"]:
            print("(no aliases defined)")
        for k, v in STATE["aliases"].items():
            print(f'alias {k}="{v}"')
        return
    joined = " ".join(args)
    if "=" not in joined:
        name = joined
        if name in STATE["aliases"]:
            print(f'alias {name}="{STATE["aliases"][name]}"')
        else:
            err(f"no such alias: {name}")
        return
    name, _, value = joined.partition("=")
    name = name.strip()
    value = value.strip().strip('"').strip("'")
    STATE["aliases"][name] = value
    save_config()
    ok(f"alias created: {name} -> {value}")


@register("unalias", "Basic Shell", "Remove an alias: unalias <name>")
def cmd_unalias(args):
    if not args:
        err("usage: unalias <name>")
        return
    name = args[0]
    if name in STATE["aliases"]:
        del STATE["aliases"][name]
        save_config()
        ok(f"alias removed: {name}")
    else:
        err(f"no such alias: {name}")


@register("set", "Basic Shell", "Set a shell variable: set name=value")
def cmd_set(args):
    if not args:
        for k, v in STATE["vars"].items():
            print(f"{k}={v}")
        return
    joined = " ".join(args)
    if "=" not in joined:
        err("usage: set name=value")
        return
    name, _, value = joined.partition("=")
    STATE["vars"][name.strip()] = value.strip().strip('"').strip("'")
    save_config()


@register("unset", "Basic Shell", "Remove a shell variable: unset <name>")
def cmd_unset(args):
    if not args:
        err("usage: unset <name>")
        return
    STATE["vars"].pop(args[0], None)
    save_config()


@register("env", "Basic Shell", "List shell variables + system environment variables")
def cmd_env(args):
    print(c("-- shell variables --", "yellow"))
    for k, v in STATE["vars"].items():
        print(f"{k}={v}")
    print(c("-- system env (first 15) --", "yellow"))
    for i, (k, v) in enumerate(os.environ.items()):
        if i >= 15:
            print("...")
            break
        print(f"{k}={v}")


@register("whoami", "Basic Shell", "Print current user")
def cmd_whoami(args):
    print(getpass.getuser())


@register("exit", "Basic Shell", "Quit MegaShell")
def cmd_exit(args):
    STATE["exit_flag"] = True


@register("date", "Basic Shell", "Print the current date and time")
def cmd_date(args):
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


@register("uptime", "Basic Shell", "Show how long MegaShell has been running")
def cmd_uptime(args):
    secs = int(time.time() - STATE["start_time"])
    print(f"{secs // 3600}h {(secs % 3600)//60}m {secs % 60}s")


# ============================================================
#  2. FILESYSTEM TOOLS
# ============================================================

@register("copy", "Filesystem", "Copy a file or directory: copy <src> <dst>")
def cmd_copy(args):
    if len(args) < 2:
        err("usage: copy <src> <dst>")
        return
    src, dst = expand_vars(args[0]), expand_vars(args[1])
    try:
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
        ok(f"copied: {src} -> {dst}")
    except Exception as e:
        err(str(e))


@register("move", "Filesystem", "Move/rename a file or directory: move <src> <dst>")
def cmd_move(args):
    if len(args) < 2:
        err("usage: move <src> <dst>")
        return
    src, dst = expand_vars(args[0]), expand_vars(args[1])
    try:
        shutil.move(src, dst)
        ok(f"moved: {src} -> {dst}")
    except Exception as e:
        err(str(e))


@register("delete", "Filesystem", "Delete a file or directory: delete <path> [-r]")
def cmd_delete(args):
    if not args:
        err("usage: delete <path> [-r]")
        return
    recursive = "-r" in args
    paths = [a for a in args if a != "-r"]
    for p in paths:
        p = expand_vars(p)
        try:
            if os.path.isdir(p):
                shutil.rmtree(p) if recursive else os.rmdir(p)
            else:
                os.remove(p)
            ok(f"deleted: {p}")
        except Exception as e:
            err(str(e))


@register("mkdir", "Filesystem", "Create a directory: mkdir <path>")
def cmd_mkdir(args):
    if not args:
        err("usage: mkdir <path>")
        return
    try:
        os.makedirs(expand_vars(args[0]), exist_ok=True)
        ok(f"created: {args[0]}")
    except Exception as e:
        err(str(e))


@register("cat", "Filesystem", "Print file contents: cat <file>")
def cmd_cat(args):
    if not args:
        err("usage: cat <file>")
        return
    try:
        with open(expand_vars(args[0]), "r", encoding="utf-8", errors="replace") as f:
            print(f.read())
    except Exception as e:
        err(str(e))


@register("write", "Filesystem", "Write text to a file: write <file> <text...>")
def cmd_write(args):
    if len(args) < 2:
        err("usage: write <file> <text...>")
        return
    path = expand_vars(args[0])
    text = " ".join(args[1:])
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        ok(f"written: {path}")
    except Exception as e:
        err(str(e))


@register("append", "Filesystem", "Append text to a file: append <file> <text...>")
def cmd_append(args):
    if len(args) < 2:
        err("usage: append <file> <text...>")
        return
    path = expand_vars(args[0])
    text = " ".join(args[1:])
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
        ok(f"appended to: {path}")
    except Exception as e:
        err(str(e))


@register("compress", "Filesystem", "Compress into a zip: compress <src> <dst.zip>")
def cmd_compress(args):
    if len(args) < 2:
        err("usage: compress <src> <dst.zip>")
        return
    src, dst = expand_vars(args[0]), expand_vars(args[1])
    try:
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
            if os.path.isdir(src):
                for root, _, files in os.walk(src):
                    for file in files:
                        full = os.path.join(root, file)
                        arc = os.path.relpath(full, os.path.dirname(src))
                        zf.write(full, arc)
            else:
                zf.write(src, os.path.basename(src))
        ok(f"compressed: {dst}")
    except Exception as e:
        err(str(e))


@register("extract", "Filesystem", "Extract an archive: extract <archive> [dest_dir]")
def cmd_extract(args):
    if not args:
        err("usage: extract <archive> [dest_dir]")
        return
    src = expand_vars(args[0])
    dst = expand_vars(args[1]) if len(args) > 1 else "."
    try:
        if src.endswith(".zip"):
            with zipfile.ZipFile(src) as zf:
                zf.extractall(dst)
        elif src.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2")):
            with tarfile.open(src) as tf:
                tf.extractall(dst)
        else:
            err("unsupported format (zip / tar / tar.gz / tar.bz2)")
            return
        ok(f"extracted to: {dst}")
    except Exception as e:
        err(str(e))


@register("search", "Filesystem", "Find files by name pattern: search <pattern> [path]")
def cmd_search(args):
    if not args:
        err("usage: search <pattern> [path]")
        return
    pattern = args[0]
    root = expand_vars(args[1]) if len(args) > 1 else "."
    found = 0
    for dirpath, _, files in os.walk(root):
        for f in files:
            if re.search(pattern, f, re.IGNORECASE):
                print(os.path.join(dirpath, f))
                found += 1
                if found >= 200:
                    print(c("... (200+ matches, truncated)", "dim"))
                    return
    if found == 0:
        print("no matches found")


@register("grep", "Filesystem", "Search text inside a file: grep <pattern> <file>")
def cmd_grep(args):
    if len(args) < 2:
        err("usage: grep <pattern> <file>")
        return
    pattern, path = args[0], expand_vars(args[1])
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                if re.search(pattern, line):
                    print(f"{c(str(i), 'dim')}: {line.rstrip()}")
    except Exception as e:
        err(str(e))


@register("watch", "Filesystem", "Watch a directory for changes (Ctrl+C to stop): watch <path>")
def cmd_watch(args):
    target = expand_vars(args[0]) if args else "."
    info(f"watching: {target}  (Ctrl+C to stop)")
    try:
        before = dict_snapshot(target)
        while True:
            time.sleep(1)
            after = dict_snapshot(target)
            for f in after:
                if f not in before:
                    print(c(f"+ created: {f}", "green"))
                elif after[f] != before[f]:
                    print(c(f"~ modified: {f}", "yellow"))
            for f in before:
                if f not in after:
                    print(c(f"- deleted: {f}", "red"))
            before = after
    except KeyboardInterrupt:
        print()
        info("watch stopped")


def dict_snapshot(path):
    snap = {}
    try:
        for f in os.listdir(path):
            full = os.path.join(path, f)
            try:
                snap[f] = os.stat(full).st_mtime
            except Exception:
                pass
    except Exception:
        pass
    return snap


@register("hash", "Filesystem", "Compute a file hash: hash <file> [md5|sha1|sha256]")
def cmd_hash(args):
    if not args:
        err("usage: hash <file> [md5|sha1|sha256]")
        return
    path = expand_vars(args[0])
    algo = args[1] if len(args) > 1 else "sha256"
    try:
        h = hashlib.new(algo)
        with open(path, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        print(f"{algo}: {h.hexdigest()}")
    except Exception as e:
        err(str(e))


@register("tree", "Filesystem", "Print a directory tree: tree [path] [depth]")
def cmd_tree(args):
    root = expand_vars(args[0]) if args else "."
    max_depth = int(args[1]) if len(args) > 1 else 3

    def walk(path, prefix, depth):
        if depth > max_depth:
            return
        try:
            entries = sorted(os.listdir(path))
        except Exception:
            return
        for i, e in enumerate(entries):
            full = os.path.join(path, e)
            last = i == len(entries) - 1
            branch = "+-- " if last else "|-- "
            label = c(e + "/", "blue") if os.path.isdir(full) else e
            print(prefix + branch + label)
            if os.path.isdir(full):
                walk(full, prefix + ("    " if last else "|   "), depth + 1)

    print(c(root, "bold"))
    walk(root, "", 1)


@register("touch", "Filesystem", "Create an empty file / update its timestamp: touch <file>")
def cmd_touch(args):
    if not args:
        err("usage: touch <file>")
        return
    path = expand_vars(args[0])
    try:
        with open(path, "a", encoding="utf-8"):
            os.utime(path, None)
        ok(f"touched: {path}")
    except Exception as e:
        err(str(e))


@register("rename", "Filesystem", "Rename a file or directory: rename <old> <new>")
def cmd_rename(args):
    if len(args) < 2:
        err("usage: rename <old> <new>")
        return
    try:
        os.rename(expand_vars(args[0]), expand_vars(args[1]))
        ok("renamed")
    except Exception as e:
        err(str(e))


@register("head", "Filesystem", "Print the first N lines of a file: head <file> [N]")
def cmd_head(args):
    if not args:
        err("usage: head <file> [N]")
        return
    n = int(args[1]) if len(args) > 1 else 10
    try:
        with open(expand_vars(args[0]), "r", encoding="utf-8", errors="replace") as f:
            for _, line in zip(range(n), f):
                print(line.rstrip())
    except Exception as e:
        err(str(e))


@register("tail", "Filesystem", "Print the last N lines of a file: tail <file> [N]")
def cmd_tail(args):
    if not args:
        err("usage: tail <file> [N]")
        return
    n = int(args[1]) if len(args) > 1 else 10
    try:
        with open(expand_vars(args[0]), "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            for line in lines[-n:]:
                print(line.rstrip())
    except Exception as e:
        err(str(e))


@register("diff", "Filesystem", "Compare two files or directories: diff <a> <b>")
def cmd_diff(args):
    if len(args) < 2:
        err("usage: diff <a> <b>")
        return
    a, b = expand_vars(args[0]), expand_vars(args[1])
    try:
        if os.path.isdir(a) and os.path.isdir(b):
            cmp = filecmp.dircmp(a, b)
            if cmp.left_only:
                print(c(f"only in {a}: {cmp.left_only}", "yellow"))
            if cmp.right_only:
                print(c(f"only in {b}: {cmp.right_only}", "yellow"))
            if cmp.diff_files:
                print(c(f"differing files: {cmp.diff_files}", "red"))
            if not (cmp.left_only or cmp.right_only or cmp.diff_files):
                ok("directories are identical")
        else:
            with open(a, "r", encoding="utf-8", errors="replace") as fa, \
                 open(b, "r", encoding="utf-8", errors="replace") as fb:
                la, lb = fa.readlines(), fb.readlines()
            import difflib
            for line in difflib.unified_diff(la, lb, fromfile=a, tofile=b):
                line = line.rstrip("\n")
                color = "green" if line.startswith("+") else "red" if line.startswith("-") else None
                print(c(line, color) if color else line)
    except Exception as e:
        err(str(e))


@register("dirsize", "Filesystem", "Total size of a directory: dirsize [path]")
def cmd_dirsize(args):
    path = expand_vars(args[0]) if args else "."
    total = 0
    count = 0
    for dirpath, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
                count += 1
            except Exception:
                pass
    print(f"{count} files, {human_size(total)}")


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


# ============================================================
#  3. NETWORKING TOOLS
# ============================================================

@register("ping", "Networking", "Test host reachability: ping <host> [count]")
def cmd_ping(args):
    if not args:
        err("usage: ping <host> [count]")
        return
    host = args[0]
    count = int(args[1]) if len(args) > 1 else 4
    flag = "-n" if platform.system() == "Windows" else "-c"
    try:
        subprocess.run(["ping", flag, str(count), host])
    except FileNotFoundError:
        for i in range(count):
            t0 = time.time()
            try:
                socket.create_connection((host, 80), timeout=2).close()
                print(f"reply from {host}: time={(time.time()-t0)*1000:.1f}ms")
            except Exception as e:
                print(f"no reply: {e}")
            time.sleep(0.3)


@register("download", "Networking", "Download a file: download <url> [dest_file]")
def cmd_download(args):
    if not args:
        err("usage: download <url> [dest_file]")
        return
    url = args[0]
    dst = args[1] if len(args) > 1 else (os.path.basename(url.split("?")[0]) or "downloaded_file")
    try:
        info(f"downloading: {url}")
        urllib.request.urlretrieve(url, dst)
        ok(f"downloaded: {dst}")
    except Exception as e:
        err(str(e))


@register("upload", "Networking", "Upload a file via HTTP POST: upload <file> <url>")
def cmd_upload(args):
    if len(args) < 2:
        err("usage: upload <file> <url>")
        return
    path, url = args[0], args[1]
    try:
        with open(path, "rb") as f:
            data = f.read()
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok(f"uploaded, response code: {resp.status}")
    except Exception as e:
        err(str(e))


@register("http", "Networking", "HTTP request: http get <url>  |  http post <url> <data>")
def cmd_http(args):
    if not args:
        err("usage: http get <url>  |  http post <url> <data>")
        return
    method = args[0].lower()
    if method == "get":
        if len(args) < 2:
            err("usage: http get <url>")
            return
        url = args[1]
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                print(c(f"HTTP {resp.status}", "green"))
                print(body[:2000])
                if len(body) > 2000:
                    print(c(f"... ({len(body)} bytes total, truncated)", "dim"))
        except Exception as e:
            err(str(e))
    elif method == "post":
        if len(args) < 3:
            err("usage: http post <url> <data>")
            return
        url = args[1]
        data = " ".join(args[2:]).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                print(c(f"HTTP {resp.status}", "green"))
                print(body[:2000])
        except Exception as e:
            err(str(e))
    else:
        err("unknown http method (get/post)")


@register("ftp", "Networking", "Connect via FTP and list files: ftp <host> [user] [pass]")
def cmd_ftp(args):
    if not args:
        err("usage: ftp <host> [user] [pass]")
        return
    import ftplib
    host = args[0]
    user = args[1] if len(args) > 1 else "anonymous"
    passwd = args[2] if len(args) > 2 else ""
    try:
        f = ftplib.FTP(host, timeout=10)
        f.login(user, passwd)
        ok(f"connected: {host}")
        for fn in f.nlst()[:50]:
            print(fn)
        f.quit()
    except Exception as e:
        err(str(e))


@register("ssh", "Networking", "SSH into a host (calls the system ssh client): ssh user@host")
def cmd_ssh(args):
    if not args:
        err("usage: ssh user@host")
        return
    try:
        subprocess.run(["ssh"] + args)
    except FileNotFoundError:
        err("no ssh client installed on this system")


@register("portscan", "Networking", "Scan TCP ports: portscan <host> [start] [end]")
def cmd_portscan(args):
    if not args:
        err("usage: portscan <host> [start] [end]")
        return
    host = args[0]
    start = int(args[1]) if len(args) > 1 else 1
    end = int(args[2]) if len(args) > 2 else 1024
    info(f"scanning: {host} ({start}-{end})")
    open_ports = []
    for port in range(start, end + 1):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.2)
        try:
            if s.connect_ex((host, port)) == 0:
                open_ports.append(port)
                print(c(f"open: {port}", "green"))
        except Exception:
            pass
        finally:
            s.close()
    if not open_ports:
        print("no open ports found in the scanned range")


@register("myip", "Networking", "Show local IP address")
def cmd_myip(args):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        print(s.getsockname()[0])
        s.close()
    except Exception as e:
        err(str(e))


@register("resolve", "Networking", "Resolve a domain to an IP: resolve <domain>")
def cmd_resolve(args):
    if not args:
        err("usage: resolve <domain>")
        return
    try:
        print(socket.gethostbyname(args[0]))
    except Exception as e:
        err(str(e))


@register("netinfo", "Networking", "Show local network interface info")
def cmd_netinfo(args):
    try:
        hostname = socket.gethostname()
        print(f"hostname: {hostname}")
        try:
            print(f"local ip (by hostname): {socket.gethostbyname(hostname)}")
        except Exception:
            pass
        try:
            import psutil
            for iface, addrs in psutil.net_if_addrs().items():
                for a in addrs:
                    if a.family == socket.AF_INET:
                        print(f"  {iface}: {a.address}")
        except ImportError:
            pass
    except Exception as e:
        err(str(e))


# ============================================================
#  4. SYSTEM MANAGEMENT
# ============================================================

@register("process", "System", "Manage processes: process list | process kill <pid>")
def cmd_process(args):
    if not args:
        err("usage: process list | process kill <pid>")
        return
    sub = args[0]
    if sub == "list":
        try:
            import psutil
            print(f"{'PID':>7}  {'NAME':<25}  {'CPU%':>6}  {'MEM%':>6}")
            for p in list(psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']))[:40]:
                d = p.info
                print(f"{d['pid']:>7}  {str(d['name'])[:25]:<25}  "
                      f"{d['cpu_percent'] or 0:>6.1f}  {d['memory_percent'] or 0:>6.1f}")
        except ImportError:
            try:
                if platform.system() == "Windows":
                    subprocess.run(["tasklist"])
                else:
                    subprocess.run(["ps", "aux"])
            except Exception as e:
                err(f"psutil not installed and 'ps' unavailable: {e}")
    elif sub == "kill":
        if len(args) < 2:
            err("usage: process kill <pid>")
            return
        try:
            pid = int(args[1])
            os.kill(pid, 9 if platform.system() != "Windows" else 15)
            ok(f"process killed: {pid}")
        except Exception as e:
            err(str(e))
    else:
        err("unknown process subcommand (list/kill)")


@register("kill", "System", "Alias for 'process kill': kill <pid>")
def cmd_kill(args):
    cmd_process(["kill"] + args)


@register("service", "System", "Manage services: service start|stop|status|list <name>")
def cmd_service(args):
    if not args:
        err("usage: service start|stop|status|list <name>")
        return
    action = args[0]
    services = STATE["services"]
    if action == "list":
        if not services:
            print("(no tracked services in this session)")
        for n, st in services.items():
            print(f"{n}: {c(st, 'green' if st == 'running' else 'red')}")
        return
    if len(args) < 2:
        err("usage: service start|stop|status <name>")
        return
    name = args[1]
    if action == "start":
        services[name] = "running"
        ok(f"service started: {name}")
    elif action == "stop":
        services[name] = "stopped"
        ok(f"service stopped: {name}")
    elif action == "status":
        print(f"{name}: {services.get(name, 'unknown')}")
    else:
        err("unknown service subcommand (start/stop/status/list)")


@register("driver", "System", "List system drivers/kernel modules: driver list")
def cmd_driver(args):
    sub = args[0] if args else "list"
    if sub != "list":
        err("usage: driver list")
        return
    system = platform.system()
    if system == "Linux":
        try:
            out = subprocess.run(["lsmod"], capture_output=True, text=True, timeout=5)
            print(out.stdout[:3000] or "(empty)")
        except Exception as e:
            err(f"lsmod unavailable: {e}")
    elif system == "Windows":
        try:
            out = subprocess.run(["driverquery"], capture_output=True, text=True, timeout=10)
            print(out.stdout[:3000])
        except Exception as e:
            err(f"driverquery unavailable: {e}")
    else:
        print("driver listing is not supported on this platform")


@register("sysinfo", "System", "Print system information")
def cmd_sysinfo(args):
    print(c("== System Information ==", "bold"))
    print(f"OS:           {platform.system()} {platform.release()}")
    print(f"Architecture: {platform.machine()}")
    print(f"Processor:    {platform.processor() or 'unknown'}")
    print(f"Python:       {platform.python_version()}")
    print(f"Hostname:     {socket.gethostname()}")
    print(f"User:         {getpass.getuser()}")
    print(f"Pkg backend:  {PKG_BACKEND or 'none detected'}")
    try:
        import psutil
        print(f"CPU cores:    {psutil.cpu_count()}")
        mem = psutil.virtual_memory()
        print(f"Memory:       {mem.used // (1024**2)}MB / {mem.total // (1024**2)}MB ({mem.percent}%)")
        disk = psutil.disk_usage('.')
        print(f"Disk:         {disk.used // (1024**3)}GB / {disk.total // (1024**3)}GB ({disk.percent}%)")
    except ImportError:
        print(c("(psutil not installed - install it for more detail: pkg install python3-psutil)", "dim"))


@register("top", "System", "Show top processes by CPU usage")
def cmd_top(args):
    try:
        import psutil
        procs = list(psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']))
        procs.sort(key=lambda p: p.info['cpu_percent'] or 0, reverse=True)
        print(f"{'PID':>7}  {'NAME':<25}  {'CPU%':>6}  {'MEM%':>6}")
        for p in procs[:15]:
            d = p.info
            print(f"{d['pid']:>7}  {str(d['name'])[:25]:<25}  {d['cpu_percent'] or 0:>6.1f}  {d['memory_percent'] or 0:>6.1f}")
    except ImportError:
        err("this requires the 'psutil' package (pkg install python3-psutil)")


@register("diskusage", "System", "Show disk usage: diskusage [path]")
def cmd_diskusage(args):
    path = expand_vars(args[0]) if args else "."
    try:
        total, used, free = shutil.disk_usage(path)
        print(f"total: {human_size(total)}  used: {human_size(used)}  free: {human_size(free)}")
    except Exception as e:
        err(str(e))


@register("run", "System", "Run a native system command: run <command...>")
def cmd_run(args):
    if not args:
        err("usage: run <command...>")
        return
    try:
        subprocess.run(args)
    except Exception as e:
        err(str(e))


@register("envinfo", "System", "Show detailed Python/OS environment info")
def cmd_envinfo(args):
    print(f"Python implementation: {platform.python_implementation()}")
    print(f"Python version:        {platform.python_version()}")
    print(f"Platform string:       {platform.platform()}")
    print(f"Executable:            {sys.executable}")


# ============================================================
#  5. PACKAGE MANAGER (REAL, not simulated)
# ============================================================
# "pkg" works just like winget, but it's called "pkg" and is cross-platform:
#   Windows -> winget
#   macOS   -> brew
#   Linux   -> apt / apt-get / dnf / yum / pacman / zypper / snap (first found)
# It shells out to the real package manager - no fake database, no progress
# bar theatre. Output is streamed live, exactly as the underlying tool prints it.

def _which(cmd):
    return shutil.which(cmd) is not None


def detect_pkg_backend():
    system = platform.system()
    if system == "Windows":
        return "winget" if _which("winget") else None
    if system == "Darwin":
        return "brew" if _which("brew") else None
    if system == "Linux":
        for backend in ("apt", "apt-get", "dnf", "yum", "pacman", "zypper", "snap"):
            if _which(backend):
                return backend
        return None
    return None


PKG_BACKEND = detect_pkg_backend()

PKG_COMMAND_MAP = {
    "winget": {
        "install": ["winget", "install", "--id", "{pkg}", "-e",
                    "--accept-source-agreements", "--accept-package-agreements"],
        "remove":  ["winget", "uninstall", "--id", "{pkg}", "-e"],
        "update":  ["winget", "upgrade", "--id", "{pkg}", "-e",
                    "--accept-source-agreements", "--accept-package-agreements"],
        "update_all": ["winget", "upgrade", "--all",
                       "--accept-source-agreements", "--accept-package-agreements"],
        "list":    ["winget", "list"],
        "search":  ["winget", "search", "{pkg}"],
        "needs_sudo": False,
    },
    "apt": {
        "install": ["apt-get", "install", "-y", "{pkg}"],
        "remove":  ["apt-get", "remove", "-y", "{pkg}"],
        "update":  ["apt-get", "install", "--only-upgrade", "-y", "{pkg}"],
        "update_all": ["apt-get", "update"],
        "list":    ["apt", "list", "--installed"],
        "search":  ["apt-cache", "search", "{pkg}"],
        "needs_sudo": True,
    },
    "apt-get": {
        "install": ["apt-get", "install", "-y", "{pkg}"],
        "remove":  ["apt-get", "remove", "-y", "{pkg}"],
        "update":  ["apt-get", "install", "--only-upgrade", "-y", "{pkg}"],
        "update_all": ["apt-get", "update"],
        "list":    ["dpkg", "-l"],
        "search":  ["apt-cache", "search", "{pkg}"],
        "needs_sudo": True,
    },
    "dnf": {
        "install": ["dnf", "install", "-y", "{pkg}"],
        "remove":  ["dnf", "remove", "-y", "{pkg}"],
        "update":  ["dnf", "upgrade", "-y", "{pkg}"],
        "update_all": ["dnf", "upgrade", "-y"],
        "list":    ["dnf", "list", "installed"],
        "search":  ["dnf", "search", "{pkg}"],
        "needs_sudo": True,
    },
    "yum": {
        "install": ["yum", "install", "-y", "{pkg}"],
        "remove":  ["yum", "remove", "-y", "{pkg}"],
        "update":  ["yum", "update", "-y", "{pkg}"],
        "update_all": ["yum", "update", "-y"],
        "list":    ["yum", "list", "installed"],
        "search":  ["yum", "search", "{pkg}"],
        "needs_sudo": True,
    },
    "pacman": {
        "install": ["pacman", "-S", "--noconfirm", "{pkg}"],
        "remove":  ["pacman", "-R", "--noconfirm", "{pkg}"],
        "update":  ["pacman", "-S", "--noconfirm", "{pkg}"],
        "update_all": ["pacman", "-Syu", "--noconfirm"],
        "list":    ["pacman", "-Q"],
        "search":  ["pacman", "-Ss", "{pkg}"],
        "needs_sudo": True,
    },
    "zypper": {
        "install": ["zypper", "install", "-y", "{pkg}"],
        "remove":  ["zypper", "remove", "-y", "{pkg}"],
        "update":  ["zypper", "update", "-y", "{pkg}"],
        "update_all": ["zypper", "update", "-y"],
        "list":    ["zypper", "search", "--installed-only"],
        "search":  ["zypper", "search", "{pkg}"],
        "needs_sudo": True,
    },
    "snap": {
        "install": ["snap", "install", "{pkg}"],
        "remove":  ["snap", "remove", "{pkg}"],
        "update":  ["snap", "refresh", "{pkg}"],
        "update_all": ["snap", "refresh"],
        "list":    ["snap", "list"],
        "search":  ["snap", "find", "{pkg}"],
        "needs_sudo": True,
    },
    "brew": {
        "install": ["brew", "install", "{pkg}"],
        "remove":  ["brew", "uninstall", "{pkg}"],
        "update":  ["brew", "upgrade", "{pkg}"],
        "update_all": ["brew", "upgrade"],
        "list":    ["brew", "list"],
        "search":  ["brew", "search", "{pkg}"],
        "needs_sudo": False,
    },
}


def build_pkg_command(action, pkg_name=None):
    spec = PKG_COMMAND_MAP[PKG_BACKEND]
    template = spec[action]
    cmd = [tok.replace("{pkg}", pkg_name) if pkg_name else tok for tok in template]
    needs_sudo = spec["needs_sudo"] and hasattr(os, "geteuid") and os.geteuid() != 0
    if needs_sudo:
        cmd = ["sudo"] + cmd
    return cmd


def run_pkg_command(cmd, label, quiet_header=True):
    """Run a real package-manager command, streaming its output live.

    quiet_header=True means we do NOT print the '$ <command>' echo line -
    only the actual output of the tool is shown, so the result isn't
    cluttered with a useless command echo.
    """
    if not quiet_header:
        info(f"$ {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
        )
        for line in proc.stdout:
            print(line, end="")
        proc.wait()
        if proc.returncode == 0:
            ok(f"{label}: done")
        else:
            err(f"{label}: failed (exit code {proc.returncode})")
        return proc.returncode == 0
    except FileNotFoundError:
        err(f"command not found on this system: '{cmd[0]}'")
        return False
    except PermissionError:
        err("permission denied - try running with admin/root privileges")
        return False
    except Exception as e:
        err(str(e))
        return False


@register("pkg", "Package Manager",
          "REAL package manager front-end (winget/apt/dnf/pacman/brew, auto-detected): "
          "pkg install|remove|update|list|search <package>")
def cmd_pkg(args):
    if PKG_BACKEND is None:
        err("no supported package manager found on this system "
            "(winget / apt / dnf / yum / pacman / zypper / snap / brew)")
        return
    if not args:
        err("usage: pkg install|remove|update|list|search <package>")
        info(f"detected backend: {PKG_BACKEND}")
        return

    sub = args[0]

    if sub == "install":
        if len(args) < 2:
            err("usage: pkg install <package>")
            return
        name = args[1]
        run_pkg_command(build_pkg_command("install", name), f"install ({name})")

    elif sub in ("remove", "uninstall"):
        if len(args) < 2:
            err("usage: pkg remove <package>")
            return
        name = args[1]
        run_pkg_command(build_pkg_command("remove", name), f"remove ({name})")

    elif sub in ("update", "upgrade"):
        if len(args) > 1:
            name = args[1]
            run_pkg_command(build_pkg_command("update", name), f"update ({name})")
        else:
            run_pkg_command(build_pkg_command("update_all"), "update all packages")

    elif sub == "list":
        run_pkg_command(build_pkg_command("list"), "list installed packages")

    elif sub == "search":
        if len(args) < 2:
            err("usage: pkg search <keyword>")
            return
        name = args[1]
        run_pkg_command(build_pkg_command("search", name), f"search ({name})")

    elif sub == "backend":
        info(f"detected package manager backend on this system: {PKG_BACKEND or 'none'}")
        info(f"platform: {platform.system()}")

    else:
        err("unknown pkg subcommand (install/remove/update/list/search/backend)")


# ============================================================
#  6. CUSTOM SCRIPTING LANGUAGE: "MSL" (MegaShell Script Language)
# ============================================================
#
# Example:
#
#   if file.exists("test.txt") {
#       print("found")
#   } else {
#       print("not found")
#   }
#
#   var x = 5
#   while x > 0 {
#       print(x)
#       x = x - 1
#   }
#
#   for i in range(0, 5) {
#       print(i)
#   }
#
#   func square(n) {
#       return n * n
#   }
#   print(square(4))
#
# Not Bash/PowerShell compatible by design - this is its own small language.

class MSLError(Exception):
    pass


class MSLReturn(Exception):
    """Internal control-flow signal used to implement 'return'."""
    def __init__(self, value):
        self.value = value


# ---- Tokenizer ----

TOKEN_SPEC = [
    ("NUMBER",   r"\d+(\.\d+)?"),
    ("STRING",   r'"(?:[^"\\]|\\.)*"'),
    ("ID",       r"[A-Za-z_][A-Za-z0-9_\.]*"),
    ("OP",       r"==|!=|<=|>=|&&|\|\||[+\-*/%=<>!(){},.;\[\]]"),
    ("NEWLINE",  r"\n"),
    ("SKIP",     r"[ \t]+"),
    ("COMMENT",  r"//[^\n]*"),
]
TOKEN_RE = re.compile("|".join(f"(?P<{n}>{p})" for n, p in TOKEN_SPEC))


def tokenize(code):
    tokens = []
    for m in TOKEN_RE.finditer(code):
        kind = m.lastgroup
        val = m.group()
        if kind in ("SKIP", "COMMENT", "NEWLINE"):
            continue
        if kind == "STRING":
            val = val[1:-1].encode().decode("unicode_escape")
        tokens.append((kind, val))
    tokens.append(("EOF", None))
    return tokens


# ---- Parser (recursive descent) -> simple tuple-based AST ----

class MSLParser:
    def __init__(self, tokens):
        self.toks = tokens
        self.pos = 0

    def peek(self):
        return self.toks[self.pos]

    def next(self):
        t = self.toks[self.pos]
        self.pos += 1
        return t

    def expect(self, val):
        kind, v = self.next()
        if v != val:
            raise MSLError(f"syntax error: expected '{val}', got '{v}'")
        return v

    def parse_program(self):
        stmts = []
        while self.peek()[0] != "EOF":
            stmts.append(self.parse_statement())
        return ("block", stmts)

    def parse_block(self):
        self.expect("{")
        stmts = []
        while self.peek()[1] != "}":
            stmts.append(self.parse_statement())
        self.expect("}")
        return ("block", stmts)

    def parse_statement(self):
        kind, val = self.peek()
        if val == "var":
            return self.parse_var()
        if val == "if":
            return self.parse_if()
        if val == "while":
            return self.parse_while()
        if val == "for":
            return self.parse_for()
        if val == "func":
            return self.parse_func()
        if val == "return":
            return self.parse_return()
        if val == "print":
            return self.parse_print()
        if val == "sh":
            return self.parse_sh()
        if val == "break":
            self.next()
            return ("break",)
        if val == "continue":
            self.next()
            return ("continue",)
        if kind == "ID":
            return self.parse_assign_or_expr()
        raise MSLError(f"unexpected token: {val}")

    def parse_var(self):
        self.next()  # var
        name = self.next()[1]
        self.expect("=")
        expr = self.parse_expr()
        return ("var", name, expr)

    def parse_assign_or_expr(self):
        name = self.next()[1]
        if self.peek()[1] == "=":
            self.next()
            expr = self.parse_expr()
            return ("assign", name, expr)
        self.pos -= 1
        expr = self.parse_expr()
        return ("exprstmt", expr)

    def parse_if(self):
        self.next()  # if
        has_paren = self.peek()[1] == "("
        if has_paren:
            self.next()
        cond = self.parse_expr()
        if has_paren:
            self.expect(")")
        then_block = self.parse_block()
        else_block = None
        if self.peek()[1] == "else":
            self.next()
            if self.peek()[1] == "if":
                else_block = ("block", [self.parse_if()])
            else:
                else_block = self.parse_block()
        return ("if", cond, then_block, else_block)

    def parse_while(self):
        self.next()  # while
        has_paren = self.peek()[1] == "("
        if has_paren:
            self.next()
        cond = self.parse_expr()
        if has_paren:
            self.expect(")")
        body = self.parse_block()
        return ("while", cond, body)

    def parse_for(self):
        # for <var> in <expr> { ... }
        self.next()  # for
        var_name = self.next()[1]
        self.expect("in")
        iterable = self.parse_expr()
        body = self.parse_block()
        return ("for", var_name, iterable, body)

    def parse_func(self):
        self.next()  # func
        name = self.next()[1]
        self.expect("(")
        params = []
        if self.peek()[1] != ")":
            params.append(self.next()[1])
            while self.peek()[1] == ",":
                self.next()
                params.append(self.next()[1])
        self.expect(")")
        body = self.parse_block()
        return ("funcdef", name, params, body)

    def parse_return(self):
        self.next()  # return
        if self.peek()[1] in ("}",):
            return ("return", ("num", 0))
        expr = self.parse_expr()
        return ("return", expr)

    def parse_print(self):
        self.next()  # print
        self.expect("(")
        args = []
        if self.peek()[1] != ")":
            args.append(self.parse_expr())
            while self.peek()[1] == ",":
                self.next()
                args.append(self.parse_expr())
        self.expect(")")
        return ("print", args)

    def parse_sh(self):
        self.next()  # sh
        self.expect("(")
        expr = self.parse_expr()
        self.expect(")")
        return ("sh", expr)

    # ---- expression parser (precedence: || -> && -> comparison -> +- -> */  -> unary -> call -> primary) ----

    def parse_expr(self):
        return self.parse_or()

    def parse_or(self):
        left = self.parse_and()
        while self.peek()[1] == "||":
            self.next()
            left = ("binop", "||", left, self.parse_and())
        return left

    def parse_and(self):
        left = self.parse_cmp()
        while self.peek()[1] == "&&":
            self.next()
            left = ("binop", "&&", left, self.parse_cmp())
        return left

    def parse_cmp(self):
        left = self.parse_add()
        while self.peek()[1] in ("==", "!=", "<", ">", "<=", ">="):
            op = self.next()[1]
            left = ("binop", op, left, self.parse_add())
        return left

    def parse_add(self):
        left = self.parse_mul()
        while self.peek()[1] in ("+", "-"):
            op = self.next()[1]
            left = ("binop", op, left, self.parse_mul())
        return left

    def parse_mul(self):
        left = self.parse_unary()
        while self.peek()[1] in ("*", "/", "%"):
            op = self.next()[1]
            left = ("binop", op, left, self.parse_unary())
        return left

    def parse_unary(self):
        if self.peek()[1] == "!":
            self.next()
            return ("not", self.parse_unary())
        if self.peek()[1] == "-":
            self.next()
            return ("neg", self.parse_unary())
        return self.parse_call()

    def parse_call(self):
        node = self.parse_primary()
        while self.peek()[1] == "(":
            self.next()
            args = []
            if self.peek()[1] != ")":
                args.append(self.parse_expr())
                while self.peek()[1] == ",":
                    self.next()
                    args.append(self.parse_expr())
            self.expect(")")
            fname = node[1] if node[0] == "id" else None
            node = ("call", fname, args)
        return node

    def parse_primary(self):
        kind, val = self.next()
        if kind == "NUMBER":
            return ("num", float(val) if "." in val else int(val))
        if kind == "STRING":
            return ("str", val)
        if kind == "ID":
            if val == "true":
                return ("bool", True)
            if val == "false":
                return ("bool", False)
            return ("id", val)
        if val == "(":
            expr = self.parse_expr()
            self.expect(")")
            return expr
        if val == "[":
            items = []
            if self.peek()[1] != "]":
                items.append(self.parse_expr())
                while self.peek()[1] == ",":
                    self.next()
                    items.append(self.parse_expr())
            self.expect("]")
            return ("list", items)
        raise MSLError(f"unexpected token in expression: {val}")


# ---- Interpreter ----

class BreakSignal(Exception):
    pass


class ContinueSignal(Exception):
    pass


class MSLInterpreter:
    def __init__(self):
        self.vars = {}
        self.funcs = {}

    def run(self, ast):
        self.exec_node(ast)

    def exec_node(self, node):
        kind = node[0]
        if kind == "block":
            for stmt in node[1]:
                self.exec_node(stmt)
        elif kind in ("var", "assign"):
            _, name, expr = node
            self.vars[name] = self.eval_node(expr)
        elif kind == "exprstmt":
            self.eval_node(node[1])
        elif kind == "if":
            _, cond, then_b, else_b = node
            if self.truthy(self.eval_node(cond)):
                self.exec_node(then_b)
            elif else_b is not None:
                self.exec_node(else_b)
        elif kind == "while":
            _, cond, body = node
            guard = 0
            while self.truthy(self.eval_node(cond)):
                try:
                    self.exec_node(body)
                except BreakSignal:
                    break
                except ContinueSignal:
                    pass
                guard += 1
                if guard > 1_000_000:
                    raise MSLError("possible infinite loop - stopped after 1M iterations")
        elif kind == "for":
            _, var_name, iterable_node, body = node
            iterable = self.eval_node(iterable_node)
            if not hasattr(iterable, "__iter__"):
                raise MSLError(f"'for' target is not iterable: {iterable!r}")
            for item in iterable:
                self.vars[var_name] = item
                try:
                    self.exec_node(body)
                except BreakSignal:
                    break
                except ContinueSignal:
                    continue
        elif kind == "funcdef":
            _, name, params, body = node
            self.funcs[name] = (params, body)
        elif kind == "return":
            raise MSLReturn(self.eval_node(node[1]))
        elif kind == "break":
            raise BreakSignal()
        elif kind == "continue":
            raise ContinueSignal()
        elif kind == "print":
            values = [self.eval_node(a) for a in node[1]]
            print(*values)
        elif kind == "sh":
            dispatch(str(self.eval_node(node[1])))
        else:
            raise MSLError(f"unknown statement: {kind}")

    def truthy(self, v):
        return bool(v)

    def eval_node(self, node):
        kind = node[0]
        if kind == "num":
            return node[1]
        if kind == "str":
            return node[1]
        if kind == "bool":
            return node[1]
        if kind == "list":
            return [self.eval_node(x) for x in node[1]]
        if kind == "id":
            if node[1] not in self.vars:
                raise MSLError(f"undefined variable: {node[1]}")
            return self.vars[node[1]]
        if kind == "neg":
            return -self.eval_node(node[1])
        if kind == "not":
            return not self.truthy(self.eval_node(node[1]))
        if kind == "binop":
            return self.eval_binop(node)
        if kind == "call":
            return self.eval_call(node)
        raise MSLError(f"unknown expression: {kind}")

    def eval_binop(self, node):
        _, op, l, r = node
        lv = self.eval_node(l)
        if op == "&&":
            return self.truthy(lv) and self.truthy(self.eval_node(r))
        if op == "||":
            return self.truthy(lv) or self.truthy(self.eval_node(r))
        rv = self.eval_node(r)
        if op == "+":
            return lv + rv
        if op == "-":
            return lv - rv
        if op == "*":
            return lv * rv
        if op == "/":
            return lv / rv
        if op == "%":
            return lv % rv
        if op == "==":
            return lv == rv
        if op == "!=":
            return lv != rv
        if op == "<":
            return lv < rv
        if op == ">":
            return lv > rv
        if op == "<=":
            return lv <= rv
        if op == ">=":
            return lv >= rv
        raise MSLError(f"unknown operator: {op}")

    def eval_call(self, node):
        _, fname, arg_nodes = node
        args = [self.eval_node(a) for a in arg_nodes]

        # user-defined function?
        if fname in self.funcs:
            params, body = self.funcs[fname]
            if len(params) != len(args):
                raise MSLError(f"{fname}() expects {len(params)} argument(s), got {len(args)}")
            saved = self.vars.copy()
            self.vars.update(dict(zip(params, args)))
            try:
                self.exec_node(body)
                result = None
            except MSLReturn as r:
                result = r.value
            self.vars = saved
            return result

        if fname == "file.exists":
            return os.path.exists(args[0])
        if fname == "file.read":
            with open(args[0], "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        if fname == "file.write":
            with open(args[0], "w", encoding="utf-8") as f:
                f.write(str(args[1]))
            return True
        if fname == "file.delete":
            os.remove(args[0])
            return True
        if fname == "file.size":
            return os.path.getsize(args[0])
        if fname == "dir.exists":
            return os.path.isdir(args[0])
        if fname == "dir.list":
            return os.listdir(args[0])
        if fname == "len":
            return len(args[0])
        if fname == "str":
            return str(args[0])
        if fname == "num":
            return float(args[0])
        if fname == "input":
            return input(args[0] if args else "")
        if fname == "range":
            if len(args) == 1:
                return list(range(int(args[0])))
            if len(args) == 2:
                return list(range(int(args[0]), int(args[1])))
            return list(range(int(args[0]), int(args[1]), int(args[2])))
        if fname == "abs":
            return abs(args[0])
        if fname == "max":
            return max(args)
        if fname == "min":
            return min(args)
        if fname == "round":
            return round(args[0], int(args[1]) if len(args) > 1 else 0)
        raise MSLError(f"unknown function: {fname}")


def run_msl_code(code):
    try:
        tokens = tokenize(code)
        parser = MSLParser(tokens)
        ast = parser.parse_program()
        interp = MSLInterpreter()
        interp.vars.update({k: try_number(v) for k, v in STATE["vars"].items()})
        interp.run(ast)
    except MSLError as e:
        err(f"MSL error: {e}")
    except MSLReturn:
        pass  # top-level stray return - ignore
    except Exception as e:
        err(f"MSL runtime error: {e}")


def try_number(s):
    try:
        return float(s) if "." in s else int(s)
    except Exception:
        return s


@register("script", "Scripting", "Run an MSL script file: script <file.msl>")
def cmd_script(args):
    if not args:
        err("usage: script <file.msl>")
        return
    path = expand_vars(args[0])
    try:
        with open(path, "r", encoding="utf-8") as f:
            code = f.read()
    except Exception as e:
        err(str(e))
        return
    run_msl_code(code)


@register("eval", "Scripting", "Run MSL code directly: eval <code>")
def cmd_eval(args):
    if not args:
        err("usage: eval <MSL code>")
        return
    run_msl_code(" ".join(args))


@register("mslhelp", "Scripting", "Show MSL scripting language syntax help")
def cmd_mslhelp(args):
    print(c("""
MSL - MegaShell Script Language (not Bash/PowerShell compatible)

  var x = 5
  x = x + 1

  if file.exists("test.txt") {
      print("found")
  } else {
      print("not found")
  }

  while x > 0 {
      print(x)
      x = x - 1
  }

  for i in range(0, 5) {
      print(i)
  }

  func square(n) {
      return n * n
  }
  print(square(4))

  sh("ls -l")          // run a built-in shell command from MSL

Built-in functions:
  file.exists(p) file.read(p) file.write(p,s) file.delete(p) file.size(p)
  dir.exists(p) dir.list(p) len(x) str(x) num(x) input(prompt)
  range(n) range(a,b) range(a,b,step) abs(x) min(...) max(...) round(x,n)

Run with:
  script myfile.msl
  eval var x = 1 print(x)
""", "cyan"))


# ============================================================
#  7. DEVELOPER / TEXT UTILITIES
# ============================================================

@register("json", "Developer Tools", "Pretty-print / validate JSON: json <file>")
def cmd_json(args):
    if not args:
        err("usage: json <file>")
        return
    try:
        with open(expand_vars(args[0]), "r", encoding="utf-8") as f:
            data = json.load(f)
        print(json.dumps(data, indent=2, ensure_ascii=False))
    except json.JSONDecodeError as e:
        err(f"invalid JSON: {e}")
    except Exception as e:
        err(str(e))


@register("base64", "Developer Tools", "Encode/decode base64: base64 encode|decode <text>")
def cmd_base64(args):
    if len(args) < 2:
        err("usage: base64 encode|decode <text>")
        return
    mode, text = args[0], " ".join(args[1:])
    try:
        if mode == "encode":
            print(base64.b64encode(text.encode()).decode())
        elif mode == "decode":
            print(base64.b64decode(text.encode()).decode())
        else:
            err("mode must be encode or decode")
    except Exception as e:
        err(str(e))


@register("uuid", "Developer Tools", "Generate a random UUID")
def cmd_uuid(args):
    print(str(uuid.uuid4()))


@register("genpass", "Developer Tools", "Generate a random password: genpass [length]")
def cmd_genpass(args):
    length = int(args[0]) if args else 16
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    print("".join(random.SystemRandom().choice(alphabet) for _ in range(length)))


@register("regex", "Developer Tools", "Test a regex against text: regex <pattern> <text>")
def cmd_regex(args):
    if len(args) < 2:
        err("usage: regex <pattern> <text>")
        return
    pattern, text = args[0], " ".join(args[1:])
    try:
        matches = re.findall(pattern, text)
        if matches:
            ok(f"{len(matches)} match(es): {matches}")
        else:
            print("no match")
    except re.error as e:
        err(f"invalid regex: {e}")


@register("wordcount", "Developer Tools", "Count words/lines/chars in a file: wordcount <file>")
def cmd_wordcount(args):
    if not args:
        err("usage: wordcount <file>")
        return
    try:
        with open(expand_vars(args[0]), "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        print(f"lines: {len(text.splitlines())}  words: {len(text.split())}  chars: {len(text)}")
    except Exception as e:
        err(str(e))


@register("timestamp", "Developer Tools", "Show / convert unix timestamps: timestamp [epoch]")
def cmd_timestamp(args):
    if not args:
        print(int(time.time()))
        return
    try:
        epoch = int(args[0])
        print(datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S"))
    except Exception as e:
        err(str(e))


@register("urlencode", "Developer Tools", "URL-encode / decode text: urlencode encode|decode <text>")
def cmd_urlencode(args):
    if len(args) < 2:
        err("usage: urlencode encode|decode <text>")
        return
    import urllib.parse
    mode, text = args[0], " ".join(args[1:])
    if mode == "encode":
        print(urllib.parse.quote(text))
    elif mode == "decode":
        print(urllib.parse.unquote(text))
    else:
        err("mode must be encode or decode")


@register("colortest", "Developer Tools", "Print a terminal color swatch test")
def cmd_colortest(args):
    for name, code in COLORS.items():
        if name in ("reset", "bold", "dim"):
            continue
        print(f"{code}{name.ljust(10)}{COLORS['reset']}", end="  ")
    print()


# ============================================================
#  8. MISC / UTILITY COMMANDS
# ============================================================

@register("calc", "Utilities", "Simple calculator: calc 2 + 2 * 3")
def cmd_calc(args):
    if not args:
        err("usage: calc <expression>")
        return
    expr = " ".join(args)
    try:
        if not re.fullmatch(r"[0-9\.\+\-\*\/\(\)\s%]+", expr):
            err("invalid character in expression")
            return
        print(eval(expr, {"__builtins__": {}}))
    except Exception as e:
        err(str(e))


@register("which", "Utilities", "Locate a command: which <command>")
def cmd_which(args):
    if not args:
        err("usage: which <command>")
        return
    name = args[0]
    if name in COMMANDS:
        print(f"{name}: built-in MegaShell command")
        return
    found = shutil.which(name)
    print(found if found else f"{name}: not found")


@register("repeat", "Utilities", "Repeat a command N times: repeat <N> <command...>")
def cmd_repeat(args):
    if len(args) < 2:
        err("usage: repeat <N> <command...>")
        return
    try:
        n = int(args[0])
    except ValueError:
        err("N must be an integer")
        return
    rest = " ".join(args[1:])
    for i in range(n):
        print(c(f"--- run {i+1}/{n} ---", "dim"))
        dispatch(rest)


@register("timeit", "Utilities", "Measure how long a command takes: timeit <command...>")
def cmd_timeit(args):
    if not args:
        err("usage: timeit <command...>")
        return
    t0 = time.time()
    dispatch(" ".join(args))
    print(c(f"elapsed: {time.time()-t0:.3f}s", "dim"))


@register("clipboard", "Utilities", "Read/write the clipboard: clipboard get|set <text>")
def cmd_clipboard(args):
    if not args:
        err("usage: clipboard get|set <text>")
        return
    try:
        import pyperclip
        if args[0] == "set":
            pyperclip.copy(" ".join(args[1:]))
            ok("copied to clipboard")
        elif args[0] == "get":
            print(pyperclip.paste())
    except ImportError:
        err("this requires the 'pyperclip' package (pip install pyperclip)")


@register("version", "Utilities", "Print the MegaShell version")
def cmd_version(args):
    print(f"MegaShell v{VERSION}")


@register("config", "Utilities", "Show/save configuration: config show|save")
def cmd_config(args):
    sub = args[0] if args else "show"
    if sub == "show":
        print(f"config file:  {STATE['config_file']}")
        print(f"history file: {STATE['history_file']}")
        print(f"pkg backend:  {PKG_BACKEND or 'none detected'}")
    elif sub == "save":
        save_config()
        ok("configuration saved")
    else:
        err("usage: config show|save")


@register("timer", "Utilities", "Countdown timer in seconds: timer <seconds>")
def cmd_timer(args):
    if not args:
        err("usage: timer <seconds>")
        return
    try:
        secs = int(args[0])
    except ValueError:
        err("seconds must be an integer")
        return
    try:
        for remaining in range(secs, 0, -1):
            print(f"\r{c(str(remaining) + 's remaining', 'yellow')}  ", end="", flush=True)
            time.sleep(1)
        print(f"\r{c('time is up!', 'green')}              ")
    except KeyboardInterrupt:
        print()
        info("timer cancelled")


@register("roll", "Utilities", "Roll dice: roll [NdM] (default 1d6)")
def cmd_roll(args):
    spec = args[0] if args else "1d6"
    m = re.fullmatch(r"(\d+)d(\d+)", spec)
    if not m:
        err("usage: roll <N>d<M>  e.g. roll 2d6")
        return
    n, sides = int(m.group(1)), int(m.group(2))
    rolls = [random.randint(1, sides) for _ in range(n)]
    print(f"{rolls}  total={sum(rolls)}")


@register("banner", "Utilities", "Print text as an ASCII banner: banner <text>")
def cmd_banner(args):
    if not args:
        err("usage: banner <text>")
        return
    text = " ".join(args)
    width = len(text) + 4
    print("+" + "-" * width + "+")
    print("|  " + text + "  |")
    print("+" + "-" * width + "+")


@register("countdown", "Utilities", "Alias for timer: countdown <seconds>")
def cmd_countdown(args):
    cmd_timer(args)


# ============================================================
#  9. TEXT PROCESSING
# ============================================================

@register("sort", "Text Processing", "Sort lines of a file: sort <file> [-r]")
def cmd_sort(args):
    if not args:
        err("usage: sort <file> [-r]")
        return
    reverse = "-r" in args
    path = expand_vars([a for a in args if a != "-r"][0])
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        for line in sorted(lines, reverse=reverse):
            print(line.rstrip())
    except Exception as e:
        err(str(e))


@register("uniq", "Text Processing", "Remove duplicate adjacent lines: uniq <file>")
def cmd_uniq(args):
    if not args:
        err("usage: uniq <file>")
        return
    try:
        with open(expand_vars(args[0]), "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        last = None
        for line in lines:
            if line != last:
                print(line.rstrip())
            last = line
    except Exception as e:
        err(str(e))


@register("replace", "Text Processing", "Find & replace in a file: replace <file> <find> <with>")
def cmd_replace(args):
    if len(args) < 3:
        err("usage: replace <file> <find> <with>")
        return
    path, find, repl = expand_vars(args[0]), args[1], args[2]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        new_content, n = re.subn(re.escape(find), repl, content)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
        ok(f"replaced {n} occurrence(s) in {path}")
    except Exception as e:
        err(str(e))


@register("upper", "Text Processing", "Convert text to UPPERCASE: upper <text>")
def cmd_upper(args):
    print(" ".join(args).upper())


@register("lower", "Text Processing", "Convert text to lowercase: lower <text>")
def cmd_lower(args):
    print(" ".join(args).lower())


@register("reverse", "Text Processing", "Reverse a string: reverse <text>")
def cmd_reverse(args):
    print(" ".join(args)[::-1])


@register("wrap", "Text Processing", "Word-wrap text to a width: wrap <width> <text...>")
def cmd_wrap(args):
    if len(args) < 2:
        err("usage: wrap <width> <text...>")
        return
    try:
        width = int(args[0])
    except ValueError:
        err("width must be an integer")
        return
    text = " ".join(args[1:])
    for line in textwrap.wrap(text, width=width):
        print(line)


@register("slugify", "Text Processing", "Convert text to a URL-friendly slug: slugify <text>")
def cmd_slugify(args):
    if not args:
        err("usage: slugify <text>")
        return
    text = " ".join(args).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    print(text)


@register("csvview", "Text Processing", "Pretty-print a CSV file as a table: csvview <file>")
def cmd_csvview(args):
    if not args:
        err("usage: csvview <file>")
        return
    import csv
    path = expand_vars(args[0])
    try:
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
            rows = list(csv.reader(f))
        if not rows:
            print("(empty file)")
            return
        widths = [max(len(str(row[i])) if i < len(row) else 0 for row in rows) for i in range(len(rows[0]))]
        for ri, row in enumerate(rows[:200]):
            line = "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))
            print(c(line, "bold") if ri == 0 else line)
            if ri == 0:
                print("  ".join("-" * w for w in widths))
        if len(rows) > 200:
            print(c(f"... ({len(rows)} rows total, truncated)", "dim"))
    except Exception as e:
        err(str(e))


# ============================================================
#  10. ENCRYPTION / ENCODING TOOLS
# ============================================================

@register("rot13", "Developer Tools", "ROT13-encode/decode text: rot13 <text>")
def cmd_rot13(args):
    if not args:
        err("usage: rot13 <text>")
        return
    import codecs
    print(codecs.encode(" ".join(args), "rot_13"))


@register("xorcrypt", "Developer Tools", "XOR-cipher text with a key: xorcrypt <key> <text>")
def cmd_xorcrypt(args):
    if len(args) < 2:
        err("usage: xorcrypt <key> <text>")
        return
    key, text = args[0], " ".join(args[1:])
    result = bytes(b ^ key.encode()[i % len(key)] for i, b in enumerate(text.encode()))
    print(base64.b64encode(result).decode())


@register("checksum", "Developer Tools", "Show md5/sha1/sha256 of a string: checksum <text>")
def cmd_checksum(args):
    if not args:
        err("usage: checksum <text>")
        return
    text = " ".join(args).encode()
    for algo in ("md5", "sha1", "sha256"):
        h = hashlib.new(algo)
        h.update(text)
        print(f"{algo}: {h.hexdigest()}")


@register("randomhex", "Developer Tools", "Generate N random hex bytes: randomhex [length]")
def cmd_randomhex(args):
    length = int(args[0]) if args else 16
    print(os.urandom(length).hex())


# ============================================================
#  11. MATH / CONVERSION TOOLS
# ============================================================

@register("convert", "Utilities", "Convert units: convert <value> <from> <to>")
def cmd_convert(args):
    if len(args) < 3:
        err("usage: convert <value> <from> <to>  (e.g. convert 10 km mi)")
        return
    try:
        value = float(args[0])
    except ValueError:
        err("value must be a number")
        return
    frm, to = args[1].lower(), args[2].lower()

    # everything normalized to a base unit per dimension
    length_to_m = {"m": 1, "km": 1000, "cm": 0.01, "mm": 0.001,
                   "mi": 1609.344, "yd": 0.9144, "ft": 0.3048, "in": 0.0254}
    weight_to_kg = {"kg": 1, "g": 0.001, "mg": 0.000001, "lb": 0.453592, "oz": 0.0283495}

    if frm in length_to_m and to in length_to_m:
        result = value * length_to_m[frm] / length_to_m[to]
        print(f"{value} {frm} = {result:g} {to}")
    elif frm in weight_to_kg and to in weight_to_kg:
        result = value * weight_to_kg[frm] / weight_to_kg[to]
        print(f"{value} {frm} = {result:g} {to}")
    elif frm in ("c", "celsius") and to in ("f", "fahrenheit"):
        print(f"{value}C = {value * 9/5 + 32:g}F")
    elif frm in ("f", "fahrenheit") and to in ("c", "celsius"):
        print(f"{value}F = {(value - 32) * 5/9:g}C")
    elif frm in ("c", "celsius") and to in ("k", "kelvin"):
        print(f"{value}C = {value + 273.15:g}K")
    elif frm in ("k", "kelvin") and to in ("c", "celsius"):
        print(f"{value}K = {value - 273.15:g}C")
    else:
        err(f"unsupported conversion: {frm} -> {to}")
        info("supported: m/km/cm/mm/mi/yd/ft/in, kg/g/mg/lb/oz, c/f/k (temperature)")


@register("baseconv", "Utilities", "Convert a number between bases: baseconv <number> <from_base> <to_base>")
def cmd_baseconv(args):
    if len(args) < 3:
        err("usage: baseconv <number> <from_base> <to_base>  (bases 2-36)")
        return
    num_str, from_base, to_base = args[0], int(args[1]), int(args[2])
    try:
        value = int(num_str, from_base)
    except ValueError:
        err(f"'{num_str}' is not valid in base {from_base}")
        return
    if to_base == 10:
        print(value)
        return
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        print("0")
        return
    out = []
    n = abs(value)
    while n:
        out.append(digits[n % to_base])
        n //= to_base
    print(("-" if value < 0 else "") + "".join(reversed(out)))


@register("primes", "Utilities", "List prime numbers up to N: primes <N>")
def cmd_primes(args):
    if not args:
        err("usage: primes <N>")
        return
    try:
        n = int(args[0])
    except ValueError:
        err("N must be an integer")
        return
    sieve = [True] * (n + 1)
    sieve[0:2] = [False, False]
    for i in range(2, int(n ** 0.5) + 1):
        if sieve[i]:
            for j in range(i * i, n + 1, i):
                sieve[j] = False
    result = [i for i, is_p in enumerate(sieve) if is_p]
    print(result)


@register("fib", "Utilities", "Print the first N Fibonacci numbers: fib <N>")
def cmd_fib(args):
    if not args:
        err("usage: fib <N>")
        return
    try:
        n = int(args[0])
    except ValueError:
        err("N must be an integer")
        return
    a, b = 0, 1
    out = []
    for _ in range(n):
        out.append(a)
        a, b = b, a + b
    print(out)


# ============================================================
#  12. MORE SYSTEM TOOLS
# ============================================================

@register("cpuinfo", "System", "Show detailed CPU information")
def cmd_cpuinfo(args):
    try:
        import psutil
        print(f"Physical cores: {psutil.cpu_count(logical=False)}")
        print(f"Logical cores:  {psutil.cpu_count(logical=True)}")
        freq = psutil.cpu_freq()
        if freq:
            print(f"Frequency:      {freq.current:.0f}MHz (min {freq.min:.0f} / max {freq.max:.0f})")
        print(f"Current usage:  {psutil.cpu_percent(interval=0.3)}%")
    except ImportError:
        print(f"Processor: {platform.processor() or 'unknown'}")
        print(c("(install psutil for more detail: pkg install python3-psutil)", "dim"))


@register("meminfo", "System", "Show detailed memory information")
def cmd_meminfo(args):
    try:
        import psutil
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        print(f"Total:     {human_size(mem.total)}")
        print(f"Used:      {human_size(mem.used)} ({mem.percent}%)")
        print(f"Available: {human_size(mem.available)}")
        print(f"Swap:      {human_size(swap.used)} / {human_size(swap.total)}")
    except ImportError:
        err("this requires the 'psutil' package (pkg install python3-psutil)")


@register("envpath", "System", "Show the PATH environment variable, one entry per line")
def cmd_envpath(args):
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        print(entry)


@register("listusers", "System", "List system users (Linux/macOS only)")
def cmd_listusers(args):
    if platform.system() == "Windows":
        err("not supported on Windows - try: run net user")
        return
    try:
        with open("/etc/passwd", "r") as f:
            for line in f:
                parts = line.split(":")
                if parts:
                    print(parts[0])
    except Exception as e:
        err(str(e))


@register("battery", "System", "Show battery status, if available")
def cmd_battery(args):
    try:
        import psutil
        b = psutil.sensors_battery()
        if b is None:
            print("no battery detected (likely a desktop system)")
            return
        status = "charging" if b.power_plugged else "discharging"
        print(f"{b.percent}% ({status})")
    except ImportError:
        err("this requires the 'psutil' package (pkg install python3-psutil)")


@register("openurl", "System", "Open a URL in the default browser: openurl <url>")
def cmd_openurl(args):
    if not args:
        err("usage: openurl <url>")
        return
    import webbrowser
    try:
        webbrowser.open(args[0])
        ok(f"opened: {args[0]}")
    except Exception as e:
        err(str(e))


@register("openfile", "System", "Open a file/folder with the default app: openfile <path>")
def cmd_openfile(args):
    if not args:
        err("usage: openfile <path>")
        return
    path = expand_vars(args[0])
    try:
        if platform.system() == "Windows":
            os.startfile(path)
        elif platform.system() == "Darwin":
            subprocess.run(["open", path])
        else:
            subprocess.run(["xdg-open", path])
        ok(f"opened: {path}")
    except Exception as e:
        err(str(e))


# ============================================================
#  13. MORE UTILITIES
# ============================================================

@register("notify", "Utilities", "Print a highlighted notification banner: notify <text>")
def cmd_notify(args):
    if not args:
        err("usage: notify <text>")
        return
    text = " ".join(args)
    print(c(f"\n*** {text} ***\n", "magenta"))


@register("choose", "Utilities", "Randomly pick one item from a list: choose a b c")
def cmd_choose(args):
    if not args:
        err("usage: choose <item1> <item2> ...")
        return
    print(random.choice(args))


@register("shuffle", "Utilities", "Shuffle a list of items: shuffle a b c")
def cmd_shuffle(args):
    if not args:
        err("usage: shuffle <item1> <item2> ...")
        return
    items = args[:]
    random.shuffle(items)
    print(" ".join(items))


@register("flip", "Utilities", "Flip a coin")
def cmd_flip(args):
    print(random.choice(["heads", "tails"]))


@register("ascii", "Utilities", "Show ASCII code of a character: ascii <char>")
def cmd_ascii(args):
    if not args:
        err("usage: ascii <char>")
        return
    ch = args[0][0]
    print(f"'{ch}' = {ord(ch)} (0x{ord(ch):02X})")


@register("stopwatch", "Utilities", "Simple stopwatch - press Enter to stop")
def cmd_stopwatch(args):
    t0 = time.time()
    info("stopwatch started - press Enter to stop")
    try:
        input()
    except KeyboardInterrupt:
        pass
    elapsed = time.time() - t0
    ok(f"elapsed: {elapsed:.2f}s")


@register("ip2bin", "Utilities", "Convert an IPv4 address to binary: ip2bin <ip>")
def cmd_ip2bin(args):
    if not args:
        err("usage: ip2bin <ip>")
        return
    try:
        parts = args[0].split(".")
        if len(parts) != 4:
            raise ValueError
        print(".".join(f"{int(p):08b}" for p in parts))
    except ValueError:
        err("invalid IPv4 address")


# ============================================================
#  14. PYTHON / DEV PROJECT TOOLING
# ============================================================
# These are convenience wrappers around real tools (pip, venv, pyinstaller,
# python -m build, etc). They call out to the actual installed tools via
# PATH - same engine as the generic PATH fallback below, but with a nicer
# command name and sane default flags. If the underlying tool isn't
# installed, you'll get a clear error telling you so.

@register("venv", "Python Tools", "Create a virtualenv: venv create [dir]  |  venv info")
def cmd_venv(args):
    if not args:
        err("usage: venv create [dir]  |  venv info")
        return
    sub = args[0]
    if sub == "create":
        target = args[1] if len(args) > 1 else "venv"
        info(f"creating virtual environment in ./{target} ...")
        proc = subprocess.run([sys.executable, "-m", "venv", target])
        if proc.returncode == 0:
            ok(f"virtualenv created: {target}")
            if platform.system() == "Windows":
                info(f"activate with: {target}\\Scripts\\activate")
            else:
                info(f"activate with: source {target}/bin/activate")
        else:
            err("venv creation failed")
    elif sub == "info":
        in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        print(f"currently in a virtualenv: {in_venv}")
        print(f"sys.prefix: {sys.prefix}")
    else:
        err("usage: venv create [dir]  |  venv info")


@register("pipinstall", "Python Tools", "Install a Python package via pip: pipinstall <package>")
def cmd_pipinstall(args):
    if not args:
        err("usage: pipinstall <package>")
        return
    cmd = [sys.executable, "-m", "pip", "install"] + args
    proc = subprocess.run(cmd)
    ok("done") if proc.returncode == 0 else err(f"pip install failed (exit {proc.returncode})")


@register("pipfreeze", "Python Tools", "Show installed Python packages (pip freeze)")
def cmd_pipfreeze(args):
    subprocess.run([sys.executable, "-m", "pip", "freeze"])


@register("pipupgrade", "Python Tools", "Upgrade a Python package via pip: pipupgrade <package>")
def cmd_pipupgrade(args):
    if not args:
        err("usage: pipupgrade <package>")
        return
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade"] + args
    proc = subprocess.run(cmd)
    ok("done") if proc.returncode == 0 else err(f"pip upgrade failed (exit {proc.returncode})")


@register("build", "Python Tools", "Build a standalone executable with PyInstaller: build <script.py>")
def cmd_build(args):
    if not args:
        err("usage: build <script.py> [--onefile]")
        return
    if shutil.which("pyinstaller") is None:
        err("pyinstaller is not installed - install it with: pipinstall pyinstaller")
        return
    script = args[0]
    extra = args[1:] if len(args) > 1 else ["--onefile"]
    cmd = ["pyinstaller"] + extra + [script]
    proc = subprocess.run(cmd)
    ok("build complete - check the dist/ folder") if proc.returncode == 0 else err("build failed")


@register("pyrun", "Python Tools", "Run a Python script with the current interpreter: pyrun <script.py> [args...]")
def cmd_pyrun(args):
    if not args:
        err("usage: pyrun <script.py> [args...]")
        return
    subprocess.run([sys.executable] + args)


@register("requirements", "Python Tools", "Generate a requirements.txt from pip freeze")
def cmd_requirements(args):
    path = args[0] if args else "requirements.txt"
    proc = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(proc.stdout)
    ok(f"written: {path}")


# ============================================================
#  15. GIT SHORTCUTS
# ============================================================
# Thin wrappers around the real `git` binary (found via PATH) with
# shorter, friendlier names. Anything not covered here still works
# directly, e.g. typing "git rebase -i HEAD~3" just works too, since
# unknown commands fall through to PATH execution automatically.

def _git(args):
    if shutil.which("git") is None:
        err("git is not installed or not on PATH")
        return None
    return subprocess.run(["git"] + args)


@register("gstatus", "Git", "Shortcut for: git status")
def cmd_gstatus(args):
    _git(["status"] + args)


@register("gadd", "Git", "Shortcut for: git add <files>")
def cmd_gadd(args):
    _git(["add"] + (args or ["."]))


@register("gcommit", "Git", 'Shortcut for: git commit -m "<message>"')
def cmd_gcommit(args):
    if not args:
        err("usage: gcommit <message>")
        return
    _git(["commit", "-m", " ".join(args)])


@register("gpush", "Git", "Shortcut for: git push")
def cmd_gpush(args):
    _git(["push"] + args)


@register("gpull", "Git", "Shortcut for: git pull")
def cmd_gpull(args):
    _git(["pull"] + args)


@register("glog", "Git", "Shortcut for: git log --oneline -n 20")
def cmd_glog(args):
    n = args[0] if args else "20"
    _git(["log", "--oneline", "-n", n])


@register("gbranch", "Git", "Shortcut for: git branch")
def cmd_gbranch(args):
    _git(["branch"] + args)


@register("gclone", "Git", "Shortcut for: git clone <url>")
def cmd_gclone(args):
    if not args:
        err("usage: gclone <url> [dir]")
        return
    _git(["clone"] + args)


@register("gdiff", "Git", "Shortcut for: git diff")
def cmd_gdiff(args):
    _git(["diff"] + args)


# ============================================================
#  16. KEY-VALUE STORE (tiny embedded database)
# ============================================================

KV_FILE = os.path.expanduser("~/.megashell_kv.json")


def _kv_load():
    if os.path.exists(KV_FILE):
        try:
            with open(KV_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _kv_save(data):
    with open(KV_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@register("kv", "Database", "Tiny key-value store: kv set|get|del|list <key> [value]")
def cmd_kv(args):
    if not args:
        err("usage: kv set|get|del|list <key> [value]")
        return
    sub = args[0]
    data = _kv_load()
    if sub == "set":
        if len(args) < 3:
            err("usage: kv set <key> <value>")
            return
        data[args[1]] = " ".join(args[2:])
        _kv_save(data)
        ok(f"set: {args[1]}")
    elif sub == "get":
        if len(args) < 2:
            err("usage: kv get <key>")
            return
        print(data.get(args[1], "(not found)"))
    elif sub == "del":
        if len(args) < 2:
            err("usage: kv del <key>")
            return
        if args[1] in data:
            del data[args[1]]
            _kv_save(data)
            ok(f"deleted: {args[1]}")
        else:
            err("key not found")
    elif sub == "list":
        if not data:
            print("(empty)")
        for k, v in data.items():
            print(f"{k} = {v}")
    else:
        err("usage: kv set|get|del|list <key> [value]")


# ============================================================
#  17. NOTES / TODO
# ============================================================

TODO_FILE = os.path.expanduser("~/.megashell_todo.json")


def _todo_load():
    if os.path.exists(TODO_FILE):
        try:
            with open(TODO_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _todo_save(items):
    with open(TODO_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


@register("todo", "Productivity", "Manage a to-do list: todo add|list|done|clear <text|index>")
def cmd_todo(args):
    if not args:
        err("usage: todo add|list|done|clear <text|index>")
        return
    sub = args[0]
    items = _todo_load()
    if sub == "add":
        if len(args) < 2:
            err("usage: todo add <text>")
            return
        items.append({"text": " ".join(args[1:]), "done": False})
        _todo_save(items)
        ok("added")
    elif sub == "list":
        if not items:
            print("(no items)")
        for i, item in enumerate(items):
            mark = "[x]" if item["done"] else "[ ]"
            color = "dim" if item["done"] else None
            line = f"{i}. {mark} {item['text']}"
            print(c(line, color) if color else line)
    elif sub == "done":
        if len(args) < 2:
            err("usage: todo done <index>")
            return
        try:
            idx = int(args[1])
            items[idx]["done"] = True
            _todo_save(items)
            ok("marked done")
        except (ValueError, IndexError):
            err("invalid index")
    elif sub == "clear":
        _todo_save([])
        ok("cleared all items")
    else:
        err("usage: todo add|list|done|clear <text|index>")


@register("note", "Productivity", "Append a quick timestamped note: note <text>")
def cmd_note(args):
    if not args:
        err("usage: note <text>")
        return
    notes_file = os.path.expanduser("~/.megashell_notes.txt")
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {' '.join(args)}\n"
    with open(notes_file, "a", encoding="utf-8") as f:
        f.write(line)
    ok("note saved")


@register("notes", "Productivity", "Show all saved notes")
def cmd_notes(args):
    notes_file = os.path.expanduser("~/.megashell_notes.txt")
    if not os.path.exists(notes_file):
        print("(no notes yet)")
        return
    with open(notes_file, "r", encoding="utf-8") as f:
        print(f.read())


# ============================================================
#  18. MARKDOWN / DOCS
# ============================================================

@register("mdpreview", "Text Processing", "Render Markdown as plain styled terminal text: mdpreview <file.md>")
def cmd_mdpreview(args):
    if not args:
        err("usage: mdpreview <file.md>")
        return
    try:
        with open(expand_vars(args[0]), "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        err(str(e))
        return
    for line in lines:
        stripped = line.rstrip("\n")
        if stripped.startswith("### "):
            print(c(stripped[4:], "cyan"))
        elif stripped.startswith("## "):
            print(c(stripped[3:].upper(), "yellow"))
        elif stripped.startswith("# "):
            print(c(stripped[2:].upper(), "bold"))
        elif stripped.startswith(("- ", "* ")):
            print("  " + c("*", "green") + " " + stripped[2:])
        elif stripped.startswith(">"):
            print(c("  | " + stripped[1:].strip(), "dim"))
        else:
            bold_stripped = re.sub(r"\*\*(.+?)\*\*", lambda m: c(m.group(1), "bold"), stripped)
            print(bold_stripped)


# ============================================================
#  19. MORE MATH / STATS
# ============================================================

@register("stats", "Utilities", "Show basic stats for a list of numbers: stats 1 2 3 4 5")
def cmd_stats(args):
    if not args:
        err("usage: stats <numbers...>")
        return
    try:
        nums = [float(a) for a in args]
    except ValueError:
        err("all arguments must be numbers")
        return
    n = len(nums)
    mean = sum(nums) / n
    sorted_nums = sorted(nums)
    median = sorted_nums[n // 2] if n % 2 else (sorted_nums[n // 2 - 1] + sorted_nums[n // 2]) / 2
    variance = sum((x - mean) ** 2 for x in nums) / n
    print(f"count:  {n}")
    print(f"sum:    {sum(nums):g}")
    print(f"mean:   {mean:g}")
    print(f"median: {median:g}")
    print(f"min:    {min(nums):g}")
    print(f"max:    {max(nums):g}")
    print(f"stddev: {variance ** 0.5:g}")


@register("gcd", "Utilities", "Greatest common divisor: gcd <a> <b>")
def cmd_gcd(args):
    if len(args) < 2:
        err("usage: gcd <a> <b>")
        return
    import math as _math
    try:
        print(_math.gcd(int(args[0]), int(args[1])))
    except ValueError:
        err("arguments must be integers")


@register("lcm", "Utilities", "Least common multiple: lcm <a> <b>")
def cmd_lcm(args):
    if len(args) < 2:
        err("usage: lcm <a> <b>")
        return
    import math as _math
    try:
        a, b = int(args[0]), int(args[1])
        print(abs(a * b) // _math.gcd(a, b))
    except ValueError:
        err("arguments must be integers")


@register("isprime", "Utilities", "Check if a number is prime: isprime <n>")
def cmd_isprime(args):
    if not args:
        err("usage: isprime <n>")
        return
    try:
        n = int(args[0])
    except ValueError:
        err("n must be an integer")
        return
    if n < 2:
        print(False)
        return
    for i in range(2, int(n ** 0.5) + 1):
        if n % i == 0:
            print(False)
            return
    print(True)


@register("factorial", "Utilities", "Compute n!: factorial <n>")
def cmd_factorial(args):
    if not args:
        err("usage: factorial <n>")
        return
    try:
        n = int(args[0])
        import math as _math
        print(_math.factorial(n))
    except ValueError:
        err("n must be a non-negative integer")


# ============================================================
#  20. LOCAL HTTP SERVER
# ============================================================

@register("serve", "Networking", "Serve the current (or given) directory over HTTP: serve [port] [dir]")
def cmd_serve(args):
    import http.server
    import socketserver
    import threading

    port = int(args[0]) if args and args[0].isdigit() else 8000
    directory = expand_vars(args[1]) if len(args) > 1 else (
        expand_vars(args[0]) if args and not args[0].isdigit() else "."
    )

    handler_cls = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=directory, **kw)
    try:
        httpd = socketserver.TCPServer(("", port), handler_cls)
    except OSError as e:
        err(f"could not bind to port {port}: {e}")
        return

    info(f"serving '{directory}' at http://localhost:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
        info("server stopped")
    finally:
        httpd.server_close()


# ============================================================
#  21. COLOR / DESIGN TOOLS
# ============================================================

@register("hex2rgb", "Developer Tools", "Convert a hex color to RGB: hex2rgb <#rrggbb>")
def cmd_hex2rgb(args):
    if not args:
        err("usage: hex2rgb <#rrggbb>")
        return
    h = args[0].lstrip("#")
    if len(h) != 6:
        err("expected a 6-digit hex color, e.g. #336699")
        return
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        print(f"rgb({r}, {g}, {b})")
        if supports_color():
            print(f"\033[48;2;{r};{g};{b}m            \033[0m  preview")
    except ValueError:
        err("invalid hex color")


@register("rgb2hex", "Developer Tools", "Convert RGB to a hex color: rgb2hex <r> <g> <b>")
def cmd_rgb2hex(args):
    if len(args) < 3:
        err("usage: rgb2hex <r> <g> <b>")
        return
    try:
        r, g, b = int(args[0]), int(args[1]), int(args[2])
        print(f"#{r:02x}{g:02x}{b:02x}")
    except ValueError:
        err("r, g, b must be integers 0-255")


@register("randomcolor", "Developer Tools", "Generate a random hex color")
def cmd_randomcolor(args):
    r, g, b = (random.randint(0, 255) for _ in range(3))
    print(f"#{r:02x}{g:02x}{b:02x}")
    if supports_color():
        print(f"\033[48;2;{r};{g};{b}m            \033[0m  preview")


# ============================================================
#  22. SCHEDULING / AUTOMATION
# ============================================================

@register("cronlist", "System", "List scheduled jobs (cron/Task Scheduler, read-only)")
def cmd_cronlist(args):
    system = platform.system()
    if system == "Windows":
        proc = subprocess.run(["schtasks", "/query", "/fo", "LIST"], capture_output=True, text=True)
        print(proc.stdout[:3000] or "(none found)")
    else:
        proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        print(proc.stdout if proc.returncode == 0 else "(no crontab for this user)")


@register("every", "Utilities", "Run a command every N seconds (Ctrl+C to stop): every <seconds> <command...>")
def cmd_every(args):
    if len(args) < 2:
        err("usage: every <seconds> <command...>")
        return
    try:
        interval = float(args[0])
    except ValueError:
        err("seconds must be a number")
        return
    rest = " ".join(args[1:])
    info(f"running every {interval}s  (Ctrl+C to stop)")
    try:
        while True:
            print(c(f"--- {datetime.now().strftime('%H:%M:%S')} ---", "dim"))
            dispatch(rest)
            time.sleep(interval)
    except KeyboardInterrupt:
        print()
        info("stopped")


# ============================================================
#  23. SECURITY / DIAGNOSTICS
# ============================================================

@register("sslcheck", "Networking", "Check a site's SSL certificate expiry: sslcheck <host>")
def cmd_sslcheck(args):
    if not args:
        err("usage: sslcheck <host> [port]")
        return
    import ssl
    host = args[0]
    port = int(args[1]) if len(args) > 1 else 443
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
        not_after = cert.get("notAfter")
        print(f"subject: {dict(x[0] for x in cert.get('subject', []))}")
        print(f"issuer:  {dict(x[0] for x in cert.get('issuer', []))}")
        print(f"expires: {not_after}")
    except Exception as e:
        err(str(e))


@register("passstrength", "Developer Tools", "Estimate password strength: passstrength <password>")
def cmd_passstrength(args):
    if not args:
        err("usage: passstrength <password>")
        return
    pw = args[0]
    score = 0
    if len(pw) >= 8:
        score += 1
    if len(pw) >= 12:
        score += 1
    if re.search(r"[a-z]", pw):
        score += 1
    if re.search(r"[A-Z]", pw):
        score += 1
    if re.search(r"[0-9]", pw):
        score += 1
    if re.search(r"[^A-Za-z0-9]", pw):
        score += 1
    labels = ["very weak", "weak", "weak", "fair", "good", "strong", "very strong"]
    label = labels[min(score, len(labels) - 1)]
    color = "red" if score <= 2 else "yellow" if score <= 4 else "green"
    print(f"length: {len(pw)}  score: {score}/6  rating: {c(label, color)}")


@register("portcheck", "Networking", "Check if a single port is open: portcheck <host> <port>")
def cmd_portcheck(args):
    if len(args) < 2:
        err("usage: portcheck <host> <port>")
        return
    host, port = args[0], int(args[1])
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    try:
        result = s.connect_ex((host, port))
        if result == 0:
            ok(f"{host}:{port} is open")
        else:
            print(f"{host}:{port} is closed or filtered")
    finally:
        s.close()


# ============================================================
#  24. MORE FILESYSTEM / BACKUP TOOLS
# ============================================================

@register("backup", "Filesystem", "Create a timestamped backup copy: backup <file_or_dir>")
def cmd_backup(args):
    if not args:
        err("usage: backup <file_or_dir>")
        return
    src = expand_vars(args[0])
    if not os.path.exists(src):
        err(f"not found: {src}")
        return
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = src.rstrip("/\\")
    dst = f"{base}.backup_{stamp}"
    try:
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        ok(f"backed up to: {dst}")
    except Exception as e:
        err(str(e))


@register("emptydir", "Filesystem", "Find empty directories under a path: emptydir [path]")
def cmd_emptydir(args):
    root = expand_vars(args[0]) if args else "."
    found = 0
    for dirpath, dirnames, filenames in os.walk(root):
        if not dirnames and not filenames:
            print(dirpath)
            found += 1
    if found == 0:
        print("no empty directories found")


@register("duplicates", "Filesystem", "Find duplicate files by content hash: duplicates [path]")
def cmd_duplicates(args):
    root = expand_vars(args[0]) if args else "."
    hashes = {}
    for dirpath, _, files in os.walk(root):
        for fname in files:
            full = os.path.join(dirpath, fname)
            try:
                h = hashlib.md5()
                with open(full, "rb") as f:
                    while chunk := f.read(65536):
                        h.update(chunk)
                digest = h.hexdigest()
                hashes.setdefault(digest, []).append(full)
            except Exception:
                pass
    found_dupes = False
    for digest, paths in hashes.items():
        if len(paths) > 1:
            found_dupes = True
            print(c(f"duplicate set ({len(paths)} files):", "yellow"))
            for p in paths:
                print(f"  {p}")
    if not found_dupes:
        print("no duplicate files found")


@register("freshness", "Filesystem", "List the N most recently modified files: freshness [path] [N]")
def cmd_freshness(args):
    root = expand_vars(args[0]) if args else "."
    n = int(args[1]) if len(args) > 1 else 10
    entries = []
    for dirpath, _, files in os.walk(root):
        for fname in files:
            full = os.path.join(dirpath, fname)
            try:
                entries.append((os.path.getmtime(full), full))
            except Exception:
                pass
    entries.sort(reverse=True)
    for mtime, path in entries[:n]:
        print(f"{datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')}  {path}")


# ============================================================
#  25. ENVIRONMENT / SHELL ENHANCEMENTS
# ============================================================

@register("pushd", "Basic Shell", "Push current dir and cd into a new one: pushd <path>")
def cmd_pushd(args):
    if not args:
        err("usage: pushd <path>")
        return
    STATE.setdefault("dir_stack", []).append(os.getcwd())
    cmd_cd(args)
    print(" ".join(STATE["dir_stack"] + [os.getcwd()]))


@register("popd", "Basic Shell", "Return to the previous directory pushed with pushd")
def cmd_popd(args):
    stack = STATE.setdefault("dir_stack", [])
    if not stack:
        err("directory stack is empty")
        return
    target = stack.pop()
    try:
        os.chdir(target)
        STATE["cwd"] = os.getcwd()
        print(os.getcwd())
    except Exception as e:
        err(str(e))


@register("dirs", "Basic Shell", "Show the directory stack (used with pushd/popd)")
def cmd_dirs(args):
    stack = STATE.get("dir_stack", [])
    print(" ".join(stack + [os.getcwd()]))


@register("source", "Basic Shell", "Run a .msh script in the current shell session: source <file>")
def cmd_source(args):
    if not args:
        err("usage: source <file>")
        return
    path = expand_vars(args[0])
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    dispatch(line)
        ok(f"sourced: {path}")
    except Exception as e:
        err(str(e))


@register("reload", "Basic Shell", "Reload aliases and variables from the config file")
def cmd_reload(args):
    load_config()
    ok("configuration reloaded")


# ============================================================
#  26. CLIPBOARD HISTORY
# ============================================================

CLIPHIST_FILE = os.path.expanduser("~/.megashell_cliphist.json")


def _cliphist_load():
    if os.path.exists(CLIPHIST_FILE):
        try:
            with open(CLIPHIST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _cliphist_save(items):
    with open(CLIPHIST_FILE, "w", encoding="utf-8") as f:
        json.dump(items[-100:], f, ensure_ascii=False, indent=2)


@register("cliphist", "Utilities", "Clipboard-style history (independent of OS clipboard): cliphist add|list|clear <text>")
def cmd_cliphist(args):
    if not args:
        err("usage: cliphist add|list|clear <text>")
        return
    sub = args[0]
    items = _cliphist_load()
    if sub == "add":
        if len(args) < 2:
            err("usage: cliphist add <text>")
            return
        text = " ".join(args[1:])
        items.append({"text": text, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        _cliphist_save(items)
        ok("added to history")
    elif sub == "list":
        if not items:
            print("(empty)")
        for i, item in enumerate(items[-20:]):
            print(f"{i}. [{item['time']}] {item['text']}")
    elif sub == "clear":
        _cliphist_save([])
        ok("history cleared")
    else:
        err("usage: cliphist add|list|clear <text>")


# ============================================================
#  27. IMAGE METADATA
# ============================================================

@register("imginfo", "Filesystem", "Show basic image file info (dimensions, format): imginfo <file>")
def cmd_imginfo(args):
    if not args:
        err("usage: imginfo <file>")
        return
    path = expand_vars(args[0])
    try:
        from PIL import Image
        with Image.open(path) as img:
            print(f"format: {img.format}")
            print(f"size:   {img.width}x{img.height}")
            print(f"mode:   {img.mode}")
        print(f"file size: {human_size(os.path.getsize(path))}")
    except ImportError:
        err("this requires the 'Pillow' package (pipinstall Pillow)")
    except Exception as e:
        err(str(e))


# ============================================================
#  28. ARCHIVE INTEGRITY
# ============================================================

@register("checkzip", "Filesystem", "Test a zip archive for corruption: checkzip <file.zip>")
def cmd_checkzip(args):
    if not args:
        err("usage: checkzip <file.zip>")
        return
    path = expand_vars(args[0])
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad is None:
                ok(f"OK - {len(zf.namelist())} file(s), no corruption detected")
            else:
                err(f"corrupt entry found: {bad}")
    except zipfile.BadZipFile:
        err("not a valid zip file")
    except Exception as e:
        err(str(e))


@register("listzip", "Filesystem", "List the contents of a zip archive without extracting: listzip <file.zip>")
def cmd_listzip(args):
    if not args:
        err("usage: listzip <file.zip>")
        return
    path = expand_vars(args[0])
    try:
        with zipfile.ZipFile(path) as zf:
            for info_obj in zf.infolist():
                print(f"{human_size(info_obj.file_size):>10}  {info_obj.filename}")
    except Exception as e:
        err(str(e))


# ============================================================
#  29. CALCULATOR REPL
# ============================================================

@register("calcmode", "Utilities", "Enter an interactive calculator REPL (type 'exit' to leave)")
def cmd_calcmode(args):
    info("calculator mode - type expressions, 'exit' to leave")
    while True:
        try:
            expr = input(c("calc> ", "magenta"))
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if expr.strip().lower() in ("exit", "quit"):
            break
        if not expr.strip():
            continue
        if not re.fullmatch(r"[0-9\.\+\-\*\/\(\)\s%]+", expr):
            err("invalid character in expression")
            continue
        try:
            print(eval(expr, {"__builtins__": {}}))
        except Exception as e:
            err(str(e))


# ============================================================
#  30. ENVIRONMENT DIFFING
# ============================================================

@register("envdiff", "System", "Snapshot or diff the environment variables: envdiff snapshot|diff")
def cmd_envdiff(args):
    snap_file = os.path.expanduser("~/.megashell_envsnap.json")
    sub = args[0] if args else "diff"
    current = dict(os.environ)
    if sub == "snapshot":
        with open(snap_file, "w", encoding="utf-8") as f:
            json.dump(current, f)
        ok(f"snapshot saved ({len(current)} variables)")
    elif sub == "diff":
        if not os.path.exists(snap_file):
            err("no snapshot found - run 'envdiff snapshot' first")
            return
        with open(snap_file, "r", encoding="utf-8") as f:
            old = json.load(f)
        added = set(current) - set(old)
        removed = set(old) - set(current)
        changed = {k for k in (set(current) & set(old)) if current[k] != old[k]}
        if added:
            print(c(f"added: {sorted(added)}", "green"))
        if removed:
            print(c(f"removed: {sorted(removed)}", "red"))
        if changed:
            print(c(f"changed: {sorted(changed)}", "yellow"))
        if not (added or removed or changed):
            ok("no differences since the last snapshot")
    else:
        err("usage: envdiff snapshot|diff")


# ============================================================
#  31. GAMES
# ============================================================
# A small built-in arcade. These use simple, dependency-free terminal
# rendering. Snake uses raw single-key input (no Enter needed) on
# platforms that support it, with a graceful turn-based fallback
# elsewhere (e.g. if stdin isn't a real interactive terminal).

def _getch_available():
    return sys.stdin.isatty() and sys.stdout.isatty()


def _read_key_nonblocking(timeout):
    """Return a single keypress within `timeout` seconds, or None.
    Works on Linux/macOS via termios/tty + select. On Windows, uses msvcrt."""
    if platform.system() == "Windows":
        try:
            import msvcrt
            start = time.time()
            while time.time() - start < timeout:
                if msvcrt.kbhit():
                    ch = msvcrt.getch()
                    try:
                        return ch.decode(errors="ignore")
                    except Exception:
                        return None
                time.sleep(0.01)
            return None
        except ImportError:
            return None
    else:
        try:
            import termios
            import tty
            import select
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setcbreak(fd)
                rlist, _, _ = select.select([sys.stdin], [], [], timeout)
                if rlist:
                    return sys.stdin.read(1)
                return None
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except Exception:
            return None


@register("snake", "Games", "Play Snake in the terminal (WASD or arrow keys, q to quit)")
def cmd_snake(args):
    if not _getch_available():
        err("snake needs an interactive terminal (stdin/stdout must be a real TTY)")
        return

    width, height = 20, 12
    snake_body = [(width // 2, height // 2)]
    direction = (1, 0)
    food = (random.randint(0, width - 1), random.randint(0, height - 1))
    score = 0
    speed = 0.15

    key_map = {
        "w": (0, -1), "s": (0, 1), "a": (-1, 0), "d": (1, 0),
        "\x1b[A": (0, -1), "\x1b[B": (0, 1), "\x1b[D": (-1, 0), "\x1b[C": (1, 0),
    }

    info("SNAKE - WASD or arrows to move, q to quit. Starting in 1s...")
    time.sleep(1)

    try:
        while True:
            key = _read_key_nonblocking(speed)
            if key:
                if key == "q":
                    break
                if key == "\x1b":
                    # try to read the rest of an escape sequence
                    k2 = _read_key_nonblocking(0.05)
                    k3 = _read_key_nonblocking(0.05)
                    seq = "\x1b" + (k2 or "") + (k3 or "")
                    if seq in key_map:
                        new_dir = key_map[seq]
                        if (new_dir[0] != -direction[0]) or (new_dir[1] != -direction[1]):
                            direction = new_dir
                elif key in key_map:
                    new_dir = key_map[key]
                    if (new_dir[0] != -direction[0]) or (new_dir[1] != -direction[1]):
                        direction = new_dir

            head_x, head_y = snake_body[0]
            new_head = (head_x + direction[0], head_y + direction[1])

            if (new_head[0] < 0 or new_head[0] >= width or
                    new_head[1] < 0 or new_head[1] >= height or
                    new_head in snake_body):
                break

            snake_body.insert(0, new_head)
            if new_head == food:
                score += 1
                while True:
                    food = (random.randint(0, width - 1), random.randint(0, height - 1))
                    if food not in snake_body:
                        break
            else:
                snake_body.pop()

            os.system("cls" if platform.system() == "Windows" else "clear")
            print(c(f"SNAKE - score: {score}  (q to quit)", "yellow"))
            print("+" + "-" * width + "+")
            for y in range(height):
                row = "|"
                for x in range(width):
                    if (x, y) == snake_body[0]:
                        row += c("@", "green")
                    elif (x, y) in snake_body:
                        row += c("o", "cyan")
                    elif (x, y) == food:
                        row += c("*", "red")
                    else:
                        row += " "
                row += "|"
                print(row)
            print("+" + "-" * width + "+")
    except KeyboardInterrupt:
        pass
    finally:
        print(c(f"\nGame over! Final score: {score}", "yellow"))


@register("guessnumber", "Games", "Guess a randomly chosen number (classic guessing game)")
def cmd_guessnumber(args):
    low, high = 1, 100
    target = random.randint(low, high)
    attempts = 0
    info(f"I'm thinking of a number between {low} and {high}. Type 'quit' to give up.")
    while True:
        try:
            guess = input("your guess> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if guess.lower() == "quit":
            info(f"the number was {target}")
            return
        try:
            n = int(guess)
        except ValueError:
            err("please enter a number")
            continue
        attempts += 1
        if n < target:
            print("higher!")
        elif n > target:
            print("lower!")
        else:
            ok(f"correct! you got it in {attempts} guesses")
            return


HANGMAN_WORDS = ["python", "shell", "keyboard", "terminal", "function",
                 "variable", "package", "network", "computer", "program"]


@register("hangman", "Games", "Play a classic game of Hangman")
def cmd_hangman(args):
    word = random.choice(HANGMAN_WORDS)
    guessed = set()
    wrong = set()
    max_wrong = 6
    stages = [
        "  +---+\n      |\n      |\n      |\n     ===",
        "  +---+\n  O   |\n      |\n      |\n     ===",
        "  +---+\n  O   |\n  |   |\n      |\n     ===",
        "  +---+\n  O   |\n /|   |\n      |\n     ===",
        "  +---+\n  O   |\n /|\\  |\n      |\n     ===",
        "  +---+\n  O   |\n /|\\  |\n /    |\n     ===",
        "  +---+\n  O   |\n /|\\  |\n / \\  |\n     ===",
    ]
    info("HANGMAN - guess the word one letter at a time")
    while True:
        display = " ".join(ch if ch in guessed else "_" for ch in word)
        print(stages[len(wrong)])
        print(f"word: {display}")
        print(f"wrong guesses: {' '.join(sorted(wrong)) or '(none)'}  ({len(wrong)}/{max_wrong})")
        if all(ch in guessed for ch in word):
            ok(f"you win! the word was '{word}'")
            return
        if len(wrong) >= max_wrong:
            err(f"you lost! the word was '{word}'")
            return
        try:
            guess = input("guess a letter> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if len(guess) != 1 or not guess.isalpha():
            err("enter a single letter")
            continue
        if guess in word:
            guessed.add(guess)
        else:
            wrong.add(guess)


@register("rps", "Games", "Play Rock-Paper-Scissors against the computer: rps [rounds]")
def cmd_rps(args):
    rounds = int(args[0]) if args and args[0].isdigit() else 3
    choices = ["rock", "paper", "scissors"]
    beats = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
    wins = losses = ties = 0
    info(f"Rock-Paper-Scissors - best effort over {rounds} round(s), type 'quit' to stop")
    for round_num in range(1, rounds + 1):
        try:
            choice = input(f"round {round_num}/{rounds} - rock/paper/scissors> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if choice == "quit":
            break
        if choice not in choices:
            err("choose rock, paper, or scissors")
            continue
        cpu = random.choice(choices)
        print(f"computer chose: {cpu}")
        if choice == cpu:
            print("tie!")
            ties += 1
        elif beats[choice] == cpu:
            ok("you win this round!")
            wins += 1
        else:
            err("you lose this round!")
            losses += 1
    print(c(f"\nfinal score - wins: {wins}  losses: {losses}  ties: {ties}", "yellow"))


@register("tictactoe", "Games", "Play Tic-Tac-Toe against the computer")
def cmd_tictactoe(args):
    board = [" "] * 9

    def render():
        rows = []
        for r in range(3):
            cells = board[r * 3:r * 3 + 3]
            rows.append(" " + " | ".join(cells) + " ")
        print(("\n" + "-" * 11 + "\n").join(rows))

    def winner():
        lines = [(0, 1, 2), (3, 4, 5), (6, 7, 8),
                 (0, 3, 6), (1, 4, 7), (2, 5, 8),
                 (0, 4, 8), (2, 4, 6)]
        for a, b, cc in lines:
            if board[a] != " " and board[a] == board[b] == board[cc]:
                return board[a]
        if " " not in board:
            return "draw"
        return None

    def cpu_move():
        empties = [i for i, v in enumerate(board) if v == " "]
        # try to win, then block, then random
        for mark, opp in [("O", "X"), ("X", "O")]:
            for i in empties:
                board[i] = "O"
                if winner() == "O":
                    return
                board[i] = " "
        board[random.choice(empties)] = "O"

    info("TIC-TAC-TOE - you are X, computer is O. Positions are numbered 1-9.")
    print(" 1 | 2 | 3 \n-----------\n 4 | 5 | 6 \n-----------\n 7 | 8 | 9 ")
    while True:
        render()
        result = winner()
        if result:
            if result == "draw":
                info("it's a draw!")
            elif result == "X":
                ok("you win!")
            else:
                err("computer wins!")
            return
        try:
            move = input("your move (1-9)> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not move.isdigit() or not (1 <= int(move) <= 9):
            err("enter a number 1-9")
            continue
        idx = int(move) - 1
        if board[idx] != " ":
            err("that square is taken")
            continue
        board[idx] = "X"
        if winner():
            continue
        cpu_move()


@register("guessword", "Games", "Wordle-style 5-letter word guessing game")
def cmd_guessword(args):
    words = ["apple", "brave", "crane", "dwell", "eager",
             "flame", "grape", "house", "input", "jolly"]
    target = random.choice(words)
    max_tries = 6
    info(f"Guess the 5-letter word ({max_tries} tries). Green=correct spot, Yellow=wrong spot.")
    for attempt in range(1, max_tries + 1):
        try:
            guess = input(f"try {attempt}/{max_tries}> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if len(guess) != 5 or not guess.isalpha():
            err("enter a 5-letter word")
            continue
        feedback = []
        for i, ch in enumerate(guess):
            if ch == target[i]:
                feedback.append(c(ch.upper(), "green"))
            elif ch in target:
                feedback.append(c(ch.upper(), "yellow"))
            else:
                feedback.append(ch.upper())
        print(" ".join(feedback))
        if guess == target:
            ok(f"you got it in {attempt} tries!")
            return
    err(f"out of tries! the word was '{target}'")


# ============================================================
#  COMMAND DISPATCHER CORE
# ============================================================

def split_command_line(line):
    try:
        return shlex.split(line)
    except ValueError:
        return line.split()


def dispatch(line):
    """Process one command line: alias resolution, > redirect, command execution."""
    line = line.strip()
    if not line or line.startswith("#"):
        return

    redirect_file = None
    append_mode = False
    if ">>" in line:
        line, _, redirect_file = line.partition(">>")
        append_mode = True
    elif ">" in line and ">=" not in line:
        parts = line.split(">")
        if len(parts) == 2 and not line.strip().startswith(("alias", "set")):
            line, redirect_file = parts[0], parts[1]

    line = line.strip()
    redirect_file = redirect_file.strip() if redirect_file else None

    parts = split_command_line(line)
    if not parts:
        return
    cmd_name, raw_args = parts[0], parts[1:]

    if cmd_name in STATE["aliases"]:
        alias_parts = split_command_line(STATE["aliases"][cmd_name])
        cmd_name, raw_args = alias_parts[0], alias_parts[1:] + raw_args

    args = [expand_vars(a) for a in raw_args]

    if cmd_name not in COMMANDS:
        run_external_command(cmd_name, args, redirect_file, append_mode)
        return

    if redirect_file:
        import io
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            COMMANDS[cmd_name]["fn"](args)
        except Exception as e:
            sys.stdout = old_stdout
            err(f"runtime error ({cmd_name}): {e}")
            return
        finally:
            sys.stdout = old_stdout
        mode = "a" if append_mode else "w"
        try:
            with open(expand_vars(redirect_file), mode, encoding="utf-8") as f:
                f.write(buf.getvalue())
            ok(f"output saved to: {redirect_file}")
        except Exception as e:
            err(str(e))
        return

    try:
        COMMANDS[cmd_name]["fn"](args)
    except Exception as e:
        err(f"runtime error ({cmd_name}): {e}")


def run_external_command(cmd_name, args, redirect_file=None, append_mode=False):
    """Fallback for any command that isn't a MegaShell built-in.

    If an executable named `cmd_name` exists on PATH (pip, pyinstaller, git,
    node, npm, docker, java, go, cargo, ... literally anything installed on
    the system), MegaShell runs it directly and streams its real output -
    just like a normal shell would. This makes MegaShell a superset of your
    normal terminal: every built-in command above PLUS every tool you have
    installed, all from one prompt.
    """
    resolved = shutil.which(cmd_name)
    if resolved is None:
        err(f"unknown command: '{cmd_name}'  (not a built-in, and not found on PATH)")
        info("type 'help' for built-in commands, or 'pkg search <name>' to install a missing tool")
        return

    full_cmd = [cmd_name] + args
    try:
        if redirect_file:
            mode = "a" if append_mode else "w"
            with open(expand_vars(redirect_file), mode, encoding="utf-8") as f:
                proc = subprocess.run(full_cmd, stdout=f, stderr=subprocess.STDOUT, text=True)
            ok(f"output saved to: {redirect_file}")
        else:
            proc = subprocess.run(full_cmd)
        if proc.returncode != 0:
            err(f"'{cmd_name}' exited with code {proc.returncode}")
    except FileNotFoundError:
        err(f"failed to execute '{cmd_name}' even though it was found on PATH")
    except PermissionError:
        err(f"permission denied running '{cmd_name}'")
    except KeyboardInterrupt:
        print()
        info(f"'{cmd_name}' interrupted")
    except Exception as e:
        err(str(e))


# ============================================================
#  TAB AUTOCOMPLETE
# ============================================================

def completer(text, state):
    options = [name for name in COMMANDS.keys() if name.startswith(text)]
    options += [name for name in STATE["aliases"].keys() if name.startswith(text)]
    options = sorted(set(options))
    if state < len(options):
        return options[state]
    return None


def setup_readline():
    if not HAS_READLINE:
        return
    readline.set_completer(completer)
    readline.parse_and_bind("tab: complete")
    try:
        readline.set_completer_delims(" \t\n")
    except Exception:
        pass


# ============================================================
#  MAIN LOOP
# ============================================================

def prompt_string():
    cwd = os.getcwd()
    home = os.path.expanduser("~")
    if cwd.startswith(home):
        cwd = "~" + cwd[len(home):]
    return c("megashell", "cyan") + c(":", "dim") + c(cwd, "blue") + c(" $ ", "dim")


def run_repl():
    load_config()
    setup_readline()
    banner()
    while not STATE["exit_flag"]:
        try:
            line = input(prompt_string())
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line.strip():
            continue
        STATE["history"].append(line)
        dispatch(line)
    save_config()
    print(c("\nGoodbye!", "yellow"))


def run_script_file(path):
    """Run a .msh command script (one MegaShell command per line) or an
    .msl MSL program file, given from the command line."""
    load_config()
    if path.endswith(".msl"):
        with open(path, "r", encoding="utf-8") as f:
            run_msl_code(f.read())
    else:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    dispatch(line)
    save_config()


def main():
    if len(sys.argv) > 1:
        script_path = sys.argv[1]
        if os.path.exists(script_path):
            run_script_file(script_path)
            return
        else:
            print(f"file not found: {script_path}")
            return
    run_repl()


if __name__ == "__main__":
    main()
