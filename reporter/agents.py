"""Live status of every agent on this PC, from cheap local reads only (no GitHub calls).

Per agent: running state, newest activity in any of its Leonida worktrees, that worktree's branch, its newest
claimed task and how many tasks and PRs it has claimed today. Worktrees are read from the main checkout's
.git/worktrees/<name>/ (HEAD for the branch, index/HEAD mtimes for activity), so no git process is started.
"""
import glob
import json
import os
import re
import subprocess
import time
from datetime import datetime

TEAM = r"Z:\Claude\team"
GITDIR = r"Z:\Claude\Leonida\.git\worktrees"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Branch prefix -> agent id. Order matters: the first match wins.
PREFIXES = [(re.compile(r"^deepseek/(\d+)-"), lambda m: "deepseek" + m.group(1)),
            (re.compile(r"^(vonsek[234])/"), lambda m: m.group(1)),
            (re.compile(r"^(cc|claude)/"), lambda m: "claude"),
            (re.compile(r"^codex/"), lambda m: "codex"),
            (re.compile(r"^ag/"), lambda m: "ag")]
AGENTS = (["claude", "codex", "ag", "vonsek2", "vonsek3", "vonsek4"] + [f"deepseek{n}" for n in range(1, 11)])
NAMES = {"claude": "Claude", "codex": "Codex", "ag": "Antigravity"}


def agent_of(branch):
    for rx, fn in PREFIXES:
        m = rx.match(branch or "")
        if m:
            return fn(m)
    return None


def worktrees():
    """{agent: (activity epoch, branch)} for the newest worktree of each agent."""
    best = {}
    for d in os.scandir(GITDIR) if os.path.isdir(GITDIR) else []:
        try:
            with open(os.path.join(d.path, "HEAD"), encoding="utf-8") as f:
                head = f.read().strip()
        except OSError:
            continue
        branch = head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") else ""
        who = agent_of(branch)
        if not who:
            continue
        t = 0
        for name in ("index", "HEAD", "logs/HEAD"):
            try:
                t = max(t, int(os.path.getmtime(os.path.join(d.path, name))))
            except OSError:
                pass
        if t > best.get(who, (0, ""))[0]:
            best[who] = (t, branch)
    return best


def deepseek_running():
    """Numbers of the Loop-DeepSeek.ps1 slingor that have a pwsh process (one process query)."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_Process -Filter \"Name='pwsh.exe'\" | "
                              "Where-Object { $_.CommandLine -match 'Loop-DeepSeek' } | "
                              "ForEach-Object { $_.CommandLine }"],
                             capture_output=True, text=True, timeout=30, creationflags=NOWIN).stdout
    except Exception:
        return set()
    return {int(m) for m in re.findall(r"-N\s+\"?(\d+)", out)}


def parse_time(s):
    try:
        return int(datetime.strptime(s, "%Y-%m-%d %H:%M").timestamp())
    except Exception:
        return 0


def claims():
    """{agent: {"today": n, "prs_today": n, "task": newest open claim}}"""
    day0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    out = {}
    for p in glob.glob(os.path.join(TEAM, "claims", "*.json")):
        try:
            with open(p, encoding="utf-8-sig") as f:
                c = json.load(f)
        except Exception:
            continue
        who = c.get("vem", "")
        t = parse_time(c.get("tid", ""))
        a = out.setdefault(who, {"today": 0, "prs_today": 0, "task": None})
        if t >= day0:
            a["today"] += 1
            if c.get("pr"):
                a["prs_today"] += 1
        if c.get("status") not in ("klar", "done", "slappt", "släppt"):
            if not a["task"] or t > a["task"]["time"]:
                a["task"] = {"id": c.get("id", ""), "pr": c.get("pr"), "time": t,
                             "text": (c.get("beskrivning") or "")[:120]}
    return out


def collect(member_state=None):
    """member_state(id) -> 'kör'/... for vonsek2-4 (from the team launcher's status files)."""
    now = int(time.time())
    wt = worktrees()
    ds = deepseek_running()
    cl = claims()
    rows = []
    for a in AGENTS:
        t, branch = wt.get(a, (0, ""))
        c = cl.get(a, {"today": 0, "prs_today": 0, "task": None})
        last = max(t, (c["task"] or {}).get("time", 0))
        if a.startswith("deepseek"):
            running = int(a[8:]) in ds
        elif a.startswith("vonsek") and member_state:
            running = member_state(a) == "kör"
        else:
            running = None  # not started from here; judged by activity on the page
        name = NAMES.get(a) or ("DeepSeek " + a[8:] if a.startswith("deepseek") else a[0].upper() + a[1:])
        rows.append({"id": a, "name": name, "running": running,
                     "last": last, "branch": branch, "task": c["task"],
                     "claims_today": c["today"], "prs_today": c["prs_today"]})
    return {"time": now, "agents": rows}


if __name__ == "__main__":
    t0 = time.time()
    d = collect()
    print(json.dumps(d, ensure_ascii=False, indent=1)[:3000])
    print(f"{time.time() - t0:.2f}s")
