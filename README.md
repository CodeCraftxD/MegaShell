# MegaShell

A single-file, do-everything command-line app written in Python. MegaShell
is a custom shell with its own scripting language, a real cross-platform
package manager front-end, full PATH passthrough for every tool you already
have installed, networking and filesystem tools, system management, Git
shortcuts, Python project tooling, a tiny key-value store, a to-do list, a
local HTTP server, and a small built-in arcade — **165 commands** in total,
all in one ~4000-line Python file with zero required dependencies.

```
megashell:~/projects $ help
```

## Why

Most terminals make you remember dozens of different tools with different
flags and conventions. MegaShell gives you one consistent, discoverable
command set (`help` lists everything, `help <command>` explains any one of
them) while still letting you drop straight into anything already on your
system — `pip`, `git`, `node`, `docker`, you name it.

## Requirements

- Don't need any of these if you use the exe! You only need this when you try the .py.
- Python 3.8+
- No required third-party packages. `psutil` and `Pillow` are optional and
  only used by a handful of commands (`process list`, `top`, `sysinfo`,
  `cpuinfo`, `meminfo`, `battery`, `imginfo`) — everything else works with
  the standard library alone.

## Highlights

### Real PATH passthrough

Any command MegaShell doesn't recognize as a built-in is checked against
your system `PATH` and run for real, with its actual output streamed live:


If a tool genuinely isn't installed, you get a clear message instead of a
crash. `which <command>` tells you whether something is a MegaShell
built-in, a resolved PATH executable, or neither.

### A real, cross-platform package manager front-end

`pkg` works like `winget`, but cross-platform. It auto-detects the right
backend for your OS and shells out to it directly — no fake database, no
progress-bar theatre:

| Platform | Detected backend(s), in order |
|----------|-------------------------------|
| Windows  | `winget` |
| macOS    | `brew` |
| Linux    | `apt` → `apt-get` → `dnf` → `yum` → `pacman` → `zypper` → `snap` |

```
pkg search python
pkg install git
pkg list
pkg update
pkg remove git
```

### Its own scripting language: MSL

Not Bash/PowerShell compatible — a small, clean language of its own, with
`var`, `if/else`, `while`, `for ... in`, user-defined `func`/`return`,
lists, and built-in file functions:

```
if file.exists("test.txt") {
    print("found")
} else {
    print("not found")
}

func square(n) {
    return n * n
}
print(square(4))

for i in range(0, 5) {
    print(i)
}
```

Run with `script file.msl`, or one-liner with `eval`. Full syntax help via
`mslhelp`.

### A small built-in arcade

```
snake          # real-time Snake, WASD or arrow keys, no Enter needed
guessnumber    # classic number-guessing game
hangman        # ASCII gallows art
rps 5          # Rock-Paper-Scissors vs the computer
tictactoe      # vs a CPU that blocks/wins when it can
guessword      # Wordle-style word guesser with color feedback
```

## All commands by category

<details>
<summary><strong>Basic Shell (21)</strong> — command interpreter, history, aliases, variables</summary>

```
alias  cd  clear  date  dirs  echo  env  exit  help  history  ls
popd  pushd  pwd  reload  set  source  unalias  unset  uptime  whoami
```
</details>

<details>
<summary><strong>Filesystem (27)</strong> — files, archives, search, backups</summary>

```
append  backup  cat  checkzip  compress  copy  delete  diff  dirsize
duplicates  emptydir  extract  freshness  grep  hash  head  imginfo
listzip  mkdir  move  rename  search  tail  touch  tree  watch  write
```
</details>

<details>
<summary><strong>Networking (13)</strong> — connectivity, transfers, diagnostics</summary>

```
download  ftp  http  myip  netinfo  ping  portcheck  portscan  resolve
serve  ssh  sslcheck  upload
```
</details>

<details>
<summary><strong>System (18)</strong> — processes, services, hardware info</summary>

```
battery  cpuinfo  cronlist  diskusage  driver  envdiff  envinfo  envpath
kill  listusers  meminfo  openfile  openurl  process  run  service
sysinfo  top
```
</details>

<details>
<summary><strong>Package Manager (1)</strong></summary>

```
pkg
```
</details>

<details>
<summary><strong>Scripting (3)</strong> — the MSL language</summary>

```
eval  mslhelp  script
```
</details>

<details>
<summary><strong>Developer Tools (17)</strong> — encoding, hashing, color, regex</summary>

```
base64  checksum  colortest  genpass  hex2rgb  json  passstrength
randomcolor  randomhex  regex  rgb2hex  rot13  timestamp  urlencode
uuid  wordcount  xorcrypt
```
</details>

<details>
<summary><strong>Utilities (29)</strong> — math, fun, productivity helpers</summary>

```
ascii  banner  baseconv  calc  calcmode  choose  clipboard  cliphist
config  convert  countdown  every  factorial  fib  flip  gcd  ip2bin
isprime  lcm  notify  primes  repeat  roll  shuffle  stats  stopwatch
timeit  timer  which
```
</details>

<details>
<summary><strong>Text Processing (10)</strong></summary>

```
csvview  lower  mdpreview  replace  reverse  slugify  sort  uniq
upper  wrap
```
</details>

<details>
<summary><strong>Python Tools (7)</strong> — venv, pip, PyInstaller wrappers</summary>

```
build  pipfreeze  pipinstall  pipupgrade  pyrun  requirements  venv
```
</details>

<details>
<summary><strong>Git (9)</strong> — short aliases for common operations</summary>

```
gadd  gbranch  gclone  gcommit  gdiff  glog  gpull  gpush  gstatus
```
</details>

<details>
<summary><strong>Database (1)</strong> — tiny key-value store</summary>

```
kv
```
</details>

<details>
<summary><strong>Productivity (3)</strong> — to-do list and notes</summary>

```
note  notes  todo
```
</details>

<details>
<summary><strong>Games (6)</strong></summary>

```
guessnumber  guessword  hangman  rps  snake  tictactoe
```
</details>

Run `help` inside MegaShell at any time for the live, up-to-date list, or `help <command>` for usage details on any single command.

## Architecture

Every command is registered into a global table with a simple decorator —
adding a new one is this easy:

```python
@register("mycommand", "Utilities", "Description shown in help")
def cmd_mycommand(args):
    print("Hello!")
```

MSL (the custom scripting language) has a complete lexer → recursive-descent
parser → AST interpreter pipeline, with zero external dependencies, pure
Python standard library.

Unknown commands fall through to a PATH-aware executor, so MegaShell's
built-ins sit on top of your existing toolchain instead of replacing it.

The `pkg` package manager is a thin, honest front-end: it builds the right
native command for your platform and streams its real output — it never
fakes results.

## Configuration & data files

MegaShell stores its small amount of local state in your home directory:

| File | Purpose |
|------|---------|
| `~/.megashell_config.json` | aliases and shell variables |
| `~/.megashell_history` | command history (readline) |
| `~/.megashell_kv.json` | `kv` key-value store |
| `~/.megashell_todo.json` | `todo` list |
| `~/.megashell_notes.txt` | `note` / `notes` log |
| `~/.megashell_cliphist.json` | `cliphist` log |
| `~/.megashell_envsnap.json` | `envdiff` snapshot |

None of these are required for MegaShell to run — they're created lazily
the first time a relevant command is used.

## License

MIT — see [LICENSE](LICENSE).
