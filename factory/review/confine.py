"""Confine the review agent: a separate user, its own copy of the inputs, no CI credentials.

Usage (as root; on GitHub Actions review.sh runs it through `sudo -n -E`):
  confine.py setup   --ws DIR --repo CHECKOUT --inputs DIR --kiro-bin DIR
  confine.py check   --ws DIR
  confine.py run     --ws DIR [--with-key] -- COMMAND...
  confine.py restore --ws DIR

The diff, the issues and every file of the checkout are untrusted, and an injected prompt can
steer the agent's read tool anywhere the agent's process can read. kiro-cli's own permission
rules are a second layer only (in kiro-cli 2.36 the v3 engine did not apply an agent-profile
`fs_read` deny to `read_file`; the v2 engine applies `deniedPaths`), so the boundary is the
operating system's:

- `setup` creates the user `factory-review` (no groups, no login shell) and a workspace, the
  agent's working directory: `repo/` (the head commit's tracked files from `git archive`, no
  `.git`, symlinks that lead out of it removed), `inputs/` (the review inputs), `home/` (the
  user's HOME, with the agent profile: tools read, grep, glob, subagent; reads allowed
  under repo/ and inputs/, denied under home/, bin/, /proc, /etc and the other system and job
  trees) and `bin/` (kiro-cli copied root-owned, mode 0711). It removes group and other access from the job's own directories (the original
  checkout with its `.git/config`, the runner's temp dirs, the invoking user's HOME) and
  records their modes for `restore`.
- Another user's /proc/<pid>/environ is unreadable (the kernel's ptrace check). kiro-cli's own
  environ holds KIRO_API_KEY, which it takes only from its environment; because its binary is
  execute-only for the user, the kernel marks the process non-dumpable, which makes its
  /proc/<pid>/environ root-owned and unreadable to the agent as well.
- `check` proves both before the agent starts and fails closed otherwise: as the user, a
  probe run from an execute-only binary must have an unreadable environ; no other process's
  environ may be readable; and no secret-looking CI variable's value (also base64-encoded)
  may appear in any file the user can read under the scanned roots or in any readable
  /proc/<pid>/cmdline.
- `run` drops to the user (no supplementary groups, no_new_privs) with an environment of
  PATH, HOME, LANG, TMPDIR and, with --with-key, the KIRO_* variables, and execs the command
  in the workspace.

Exit 0 on success; 5 when confinement cannot be set up or does not hold (review.sh then fails
closed). Findings name variables and paths, never values.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import pwd
import shutil
import stat
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from redact import secrets  # noqa: E402

USER = "factory-review"
AGENT = "factory-review"
FAIL = 5
MAX_FILE = 4 * 1024 * 1024
SCAN_BUDGET_S = 120.0
# Roots the scan walks as the review user. System trees (/usr, /lib, /opt, /proc, /sys, /dev)
# hold no job credentials and are left out to keep the scan fast; /proc is checked per process.
SCAN_ROOTS = ["/tmp", "/var/tmp", "/run", "/etc", "/root", "/home", "/builds", "/srv", "/mnt"]


class ConfineError(Exception):
    pass


# ---------------------------------------------------------------- pure helpers (tested)


# The tools the agent gets, by their kiro-cli 2.x (agent engine v2) names: review.sh pins that
# engine, the one whose stream-json reports credits. No shell, no write, no web, no MCP.
# Read-only tools only: `code` can rewrite files (pattern_rewrite), its own profile included.
TOOLS = ["read", "grep", "glob", "subagent"]


def denied_paths(ws: str) -> list[str]:
    """Paths the read tools refuse even though the user could open them."""
    return [f"{ws}/home/**", f"{ws}/bin/**", "/proc/**", "/sys/**", "/dev/**", "/etc/**",
            "/root/**", "/home/**", "/builds/**", "/run/**", "/var/**", "/mnt/**", "/srv/**"]


def agent_config(ws: str) -> dict:
    """The kiro-cli agent profile: read-only tools over the two copies, nothing else."""
    readable = [f"{ws}/repo/**", f"{ws}/inputs/**"]
    settings = {"allowedPaths": readable, "deniedPaths": denied_paths(ws)}
    return {
        "name": AGENT,
        "description": "Read-only merge request reviewer (factory CI).",
        "prompt": "You review a merge request. You can read only the review inputs and the "
                  "checkout copy named in the task.",
        "tools": list(TOOLS),
        "allowedTools": list(TOOLS),
        "includeMcpJson": False,
        "mcpServers": {},
        "resources": [],
        "toolsSettings": {"read": settings, "grep": settings, "glob": settings,
                          "subagent": {"availableAgents": [AGENT], "trustedAgents": [AGENT]}},
    }


def agent_env(env: dict[str, str], ws: str, with_key: bool) -> dict[str, str]:
    """The confined process's whole environment."""
    home = os.path.join(ws, "home")
    out = {"PATH": f"{ws}/bin:/usr/local/bin:/usr/bin:/bin", "HOME": home, "USER": USER,
           "LOGNAME": USER, "LANG": "C.UTF-8", "TERM": "dumb", "NO_COLOR": "1",
           "TMPDIR": os.path.join(home, "tmp")}
    if with_key:
        out.update({k: v for k, v in env.items() if k.startswith("KIRO_")})
    return out


def lock_targets(env: dict[str, str], invoker_home: str | None) -> list[str]:
    """The job's own directories the review user must not enter."""
    cands = []
    for k in ("CI_PROJECT_DIR", "GITHUB_WORKSPACE", "RUNNER_TEMP"):
        if env.get(k):
            cands.append(env[k])
    if env.get("CI_PROJECT_DIR"):
        cands.append(env["CI_PROJECT_DIR"].rstrip("/") + ".tmp")  # file variables, CA files
    if env.get("GITHUB_EVENT_PATH"):
        cands.append(os.path.dirname(env["GITHUB_EVENT_PATH"]))
    if invoker_home:
        cands.append(invoker_home)
    out: list[str] = []
    for c in cands:
        c = os.path.realpath(c)
        if c not in ("/", "/tmp", "/var/tmp", "/home", "/builds") and c not in out:
            out.append(c)
    return out


def needles(env: dict[str, str]) -> dict[str, list[bytes]]:
    """{name: byte strings to look for}: each secret value, raw and base64-encoded."""
    out: dict[str, list[bytes]] = {}
    for name, val in secrets(env).items():
        raw = val.encode()
        ns = [raw]
        for shift in range(3):  # the value starts at any offset inside a base64 stream
            enc = base64.b64encode(b"\0" * shift + raw)
            lead = (shift * 4 + 2) // 3  # chars that depend on the padding bytes
            core = enc.rstrip(b"=")
            core = core[lead:len(core) - 2]  # the last chars depend on what follows
            if len(core) >= 8:
                ns.append(core)
        out[name] = ns
    return out


def scan_files(roots: list[str], ndl: dict[str, list[bytes]], skip: list[str],
               budget_s: float = SCAN_BUDGET_S) -> tuple[list[tuple[str, str]], int]:
    """Readable regular files under roots that hold a needle: ([(path, name)], files read)."""
    found: list[tuple[str, str]] = []
    seen, n = set(), 0
    deadline = time.monotonic() + budget_s
    skip = [os.path.realpath(s) for s in skip]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for top, dirs, files in os.walk(root, onerror=lambda e: None, followlinks=False):
            if time.monotonic() > deadline:
                raise ConfineError(f"secret scan did not finish in {budget_s:.0f}s (at {top})")
            rtop = os.path.realpath(top)
            if any(rtop == s or rtop.startswith(s + "/") for s in skip):
                dirs[:] = []
                continue
            for f in files:
                p = os.path.join(top, f)
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE or (st.st_dev, st.st_ino) in seen:
                    continue
                seen.add((st.st_dev, st.st_ino))
                data = _read(p)
                if data is None:
                    continue
                n += 1
                found += [(p, name) for name, ns in ndl.items() if any(x in data for x in ns)]
    return found, n


def remove_escaping_symlinks(root: str) -> int:
    """Delete symlinks under root that resolve outside it (or dangle). Returns how many."""
    rroot, n = os.path.realpath(root), 0
    for top, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            p = os.path.join(top, name)
            if os.path.islink(p):
                target = os.path.realpath(p)
                if not (target == rroot or target.startswith(rroot + "/")) or not os.path.exists(p):
                    os.unlink(p)
                    n += 1
    return n


def _read(p: str) -> bytes | None:
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        return os.read(fd, MAX_FILE + 1)
    except OSError:
        return None
    finally:
        os.close(fd)


# ---------------------------------------------------------------- privileged steps


def _need_root() -> None:
    if os.geteuid() != 0:
        raise ConfineError("needs root (GitLab's docker runner job is root; on GitHub Actions "
                           "review.sh calls this through sudo -n -E)")


def _user() -> pwd.struct_passwd:
    try:
        u = pwd.getpwnam(USER)
    except KeyError:
        raise ConfineError(f"user {USER} does not exist") from None
    if u.pw_uid == 0 or u.pw_gid == 0:
        raise ConfineError(f"user {USER} has uid or gid 0")
    return u


def _drop(u: pwd.struct_passwd) -> None:
    """In the current process: become the user for good."""
    os.setgroups([])
    os.setgid(u.pw_gid)
    os.setuid(u.pw_uid)
    if os.getuid() != u.pw_uid or os.geteuid() != u.pw_uid or os.getgroups() not in ([], [u.pw_gid]):
        raise ConfineError("could not drop to the review user")
    try:  # PR_SET_NO_NEW_PRIVS: no setuid binary can give root back
        ctypes.CDLL(None, use_errno=True).prctl(38, 1, 0, 0, 0)
    except (OSError, AttributeError):
        pass


def copy_checkout(repo: str, dest: str, idx: str) -> None:
    """The head commit's tracked files into `dest`, without .git (its config can hold the job token).

    Every tracked file: `git archive` would honour the merge request's own `.gitattributes`
    (`export-ignore`, `export-subst`) and could leave changed code out of the review. A throwaway
    index (`idx`) keeps the job's own index untouched.
    """
    env = dict(os.environ, GIT_INDEX_FILE=idx)
    try:
        for cmd in (["git", "-C", repo, "read-tree", "HEAD"],
                    ["git", "-C", repo, "checkout-index", "-a", "-f", "--prefix=" + dest.rstrip("/") + "/"]):
            r = subprocess.run(cmd, env=env, capture_output=True)
            if r.returncode != 0:
                raise ConfineError(f"git {cmd[3]} failed: " + r.stderr.decode(errors="replace").strip())
    finally:
        if os.path.exists(idx):
            os.remove(idx)


def setup(ws: str, repo: str, inputs: str, kiro_bin: str) -> None:
    _need_root()
    try:
        pwd.getpwnam(USER)
    except KeyError:
        r = subprocess.run(["useradd", "--system", "--user-group", "--no-create-home",
                            "--home-dir", os.path.join(ws, "home"), "--shell", "/usr/sbin/nologin",
                            USER], capture_output=True, text=True)
        if r.returncode != 0:
            raise ConfineError(f"useradd failed: {r.stderr.strip()}")
    u = _user()
    os.makedirs(ws, exist_ok=True)
    os.chown(ws, 0, 0)
    os.chmod(ws, 0o755)
    for d in ("repo", "inputs", "bin", "home"):
        p = os.path.join(ws, d)
        if os.path.exists(p):
            shutil.rmtree(p)
        os.mkdir(p)
    copy_checkout(repo, os.path.join(ws, "repo"), os.path.join(ws, "checkout.index"))
    removed = remove_escaping_symlinks(os.path.join(ws, "repo"))
    shutil.copytree(inputs, os.path.join(ws, "inputs"), dirs_exist_ok=True, symlinks=False)
    for d in ("repo", "inputs"):
        subprocess.run(["chown", "-R", "-h", "0:0", os.path.join(ws, d)], check=True)
        subprocess.run(["chmod", "-R", "u+rwX,go+rX,go-w", os.path.join(ws, d)], check=True)
    # kiro-cli, execute-only for the user: the kernel then makes its processes non-dumpable.
    copied = 0
    for name in sorted(os.listdir(kiro_bin)):
        src = os.path.join(kiro_bin, name)
        if name.startswith("kiro-cli") and os.path.isfile(src):
            dst = os.path.join(ws, "bin", name)
            shutil.copyfile(os.path.realpath(src), dst)
            os.chown(dst, 0, 0)
            os.chmod(dst, 0o711)
            copied += 1
    if not copied:
        raise ConfineError(f"no kiro-cli binary in {kiro_bin}")
    os.chmod(os.path.join(ws, "bin"), 0o711)
    home = os.path.join(ws, "home")
    for d in (".kiro/agents", "tmp"):
        os.makedirs(os.path.join(home, d), exist_ok=True)
    with open(os.path.join(home, ".kiro/agents", AGENT + ".json"), "w", encoding="utf-8") as fh:
        json.dump(agent_config(ws), fh, indent=1)
    subprocess.run(["chown", "-R", "-h", f"{u.pw_uid}:{u.pw_gid}", home], check=True)
    os.chmod(home, 0o700)
    # Lock the job's own directories; remember their modes for restore.
    invoker = os.environ.get("SUDO_USER")
    invoker_home = pwd.getpwnam(invoker).pw_dir if invoker else os.path.expanduser("~")
    state = {}
    for t in lock_targets(dict(os.environ), invoker_home):
        if os.path.isdir(t) and not os.path.islink(t):
            mode = stat.S_IMODE(os.stat(t).st_mode)
            state[t] = mode
            os.chmod(t, mode & 0o700)
    spath = os.path.join(ws, "lock-state.json")
    with open(spath, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.chmod(spath, 0o600)
    print(f"confine: agent user {USER} (uid {u.pw_uid}, no groups), workspace {ws}; kiro-cli "
          f"execute-only ({copied} file(s)); {removed} symlink(s) leading out of the checkout "
          f"removed; locked {len(state)} job dir(s): {', '.join(sorted(state)) or '-'}")


def _as_user(fn, *args):
    """Run fn(*args) in a child process as the review user; return its JSON result."""
    u = _user()
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        try:
            _drop(u)
            res = {"ok": True, "value": fn(*args)}
        except Exception as e:  # noqa: BLE001 - reported to the parent
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        os.write(w, json.dumps(res).encode())
        os._exit(0)
    os.close(w)
    chunks = []
    while True:
        b = os.read(r, 65536)
        if not b:
            break
        chunks.append(b)
    os.close(r)
    os.waitpid(pid, 0)
    res = json.loads(b"".join(chunks) or b'{"ok": false, "error": "no result"}')
    if not res["ok"]:
        raise ConfineError(res["error"])
    return res["value"]


def _user_view(ws: str, ndl: dict[str, list[str]]) -> dict:
    """As the user: what of the job's secrets can it reach?"""
    bndl = {k: [base64.b64decode(x) for x in v] for k, v in ndl.items()}
    environ = []
    cmdline = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        if _read(f"/proc/{d}/environ") is not None and int(d) != os.getpid():
            environ.append(int(d))
        data = _read(f"/proc/{d}/cmdline")
        if data:
            cmdline += [(f"/proc/{d}/cmdline", n) for n, ns in bndl.items() if any(x in data for x in ns)]
    found, n = scan_files(SCAN_ROOTS + [ws], bndl, skip=[os.path.join(ws, "bin")])
    return {"environ_readable": environ, "cmdline": cmdline, "files": found, "files_read": n,
            "uid": os.getuid()}


def check(ws: str) -> None:
    _need_root()
    u = _user()
    ndl = needles(dict(os.environ))
    if not ndl:
        raise ConfineError("no secret-looking CI variable in the environment to check against")
    # A probe from an execute-only binary, run as the user: its environ must be unreadable.
    probe = os.path.join(ws, "bin", ".probe")
    sleep = shutil.which("sleep") or "/bin/sleep"
    shutil.copyfile(os.path.realpath(sleep), probe)
    os.chown(probe, 0, 0)
    os.chmod(probe, 0o711)
    pid = os.fork()
    if pid == 0:
        try:
            _drop(u)
            os.execve(probe, [probe, "60"], {"FACTORY_PROBE": "1", "PATH": "/usr/bin:/bin"})
        finally:
            os._exit(127)
    try:
        time.sleep(0.5)
        if os.waitpid(pid, os.WNOHANG) != (0, 0):
            raise ConfineError("the execute-only probe did not start as the review user")
        wire = {k: [base64.b64encode(x).decode() for x in v] for k, v in ndl.items()}
        view = _as_user(_user_view, ws, wire)
    finally:
        try:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
        except OSError:
            pass
        os.unlink(probe)
    bad = []
    if pid in view["environ_readable"]:
        bad.append("the environ of an execute-only process is readable by its own user, so "
                   "kiro-cli's KIRO_API_KEY would be readable by the agent")
    others = [p for p in view["environ_readable"] if p != pid]
    if others:
        bad.append(f"/proc/<pid>/environ readable for {len(others)} other process(es): {others[:10]}")
    for p, name in view["cmdline"]:
        bad.append(f"{name} readable in {p}")
    for p, name in view["files"]:
        bad.append(f"{name} readable in {p}")
    if bad:
        raise ConfineError("confinement does not hold: " + "; ".join(bad))
    print(f"confine: check passed as uid {view['uid']}: no other process environment readable, "
          f"execute-only kiro-cli is non-dumpable, {len(ndl)} CI secret value(s) found in none of "
          f"{view['files_read']} readable files under {', '.join(r for r in SCAN_ROOTS + [ws] if os.path.isdir(r))} "
          f"or any process command line")


def run(ws: str, with_key: bool, cmd: list[str]) -> None:
    _need_root()
    u = _user()
    env = agent_env(dict(os.environ), ws, with_key)
    # The workspace itself is the working directory: kiro-cli's subagents read without asking
    # only under it, and it holds no README.md, AGENTS.md or .kiro of the merge request's own.
    os.chdir(ws)
    _drop(u)
    os.execvpe(cmd[0], cmd, env)


def restore(ws: str) -> None:
    _need_root()
    spath = os.path.join(ws, "lock-state.json")
    if not os.path.isfile(spath):
        return
    for t, mode in json.load(open(spath, encoding="utf-8")).items():
        if os.path.isdir(t):
            os.chmod(t, mode)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="confine.py")
    ap.add_argument("step", choices=["setup", "check", "run", "restore"])
    ap.add_argument("--ws", required=True)
    ap.add_argument("--repo")
    ap.add_argument("--inputs")
    ap.add_argument("--kiro-bin")
    ap.add_argument("--with-key", action="store_true")
    args = argv[1:]
    cmd = args[args.index("--") + 1:] if "--" in args else []
    a = ap.parse_args(args[:args.index("--")] if "--" in args else args)
    try:
        if a.step == "setup":
            if not (a.repo and a.inputs and a.kiro_bin):
                raise ConfineError("setup needs --repo, --inputs and --kiro-bin")
            setup(os.path.abspath(a.ws), a.repo, a.inputs, a.kiro_bin)
        elif a.step == "check":
            check(os.path.abspath(a.ws))
        elif a.step == "run":
            if not cmd:
                raise ConfineError("run needs a command after --")
            run(os.path.abspath(a.ws), a.with_key, cmd)
        else:
            restore(os.path.abspath(a.ws))
    except (ConfineError, OSError, subprocess.CalledProcessError) as e:
        print(f"confine {a.step}: {e}", file=sys.stderr)
        return FAIL
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
