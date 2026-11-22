"""Publishes fresh team data and the local Claude Code terminals' progress to the team site.

Runs on Lucas's PC every 2 minutes: rebuilds data.json (our PRs, pushes, progress;
leaderboard every 15 min) and uploads it to the `live` branch of Vonsek/teampanel. Only publishes: member id, whether the
terminal is running, last activity time, branch, number of changed files,
last commit subject/time and the claimed task. No paths, logs or prompts.

Start: python Z:\\Claude\\teampanel\\reporter\\rapport.py   (Ctrl+C stops)
Once:  python Z:\\Claude\\teampanel\\reporter\\rapport.py --once
"""
import base64
import glob
import importlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime

TEAM = r"Z:\Claude\team"
KONTON = r"Z:\Claude\konton"
MEMBERS = ["vonsek2", "vonsek3", "vonsek4"]
REPO = "Vonsek/teampanel"
BRANCH = "live"
PATH = "data.json"
FULL_EVERY = 15 * 60
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INTERVAL = 120
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(args, inp=None):
    r = subprocess.run(args, input=inp, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", creationflags=NOWIN, timeout=60)
    return r.returncode, r.stdout.strip()


def git(path, *args):
    return run(["git", "--no-optional-locks", "-C", path, *args])[1]


def pid_alive(pid):
    if not pid:
        return False
    code, out = run(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH", "/FO", "CSV"])
    return f'"{int(pid)}"' in out


def parse_time(s):
    try:
        return int(datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp())
    except Exception:
        try:
            return int(datetime.strptime(s, "%Y-%m-%d %H:%M").timestamp())
        except Exception:
            return 0


def member_status(mid):
    st = {}
    try:
        with open(os.path.join(TEAM, "status", mid + ".json"), encoding="utf-8-sig") as f:
            st = json.load(f)
    except Exception:
        pass
    logs = glob.glob(os.path.join(KONTON, mid, "projects", "*", "*.jsonl"))
    last_session = max([int(os.path.getmtime(p)) for p in logs] + [0])

    wt = os.path.join(r"Z:\Claude", "Leonida-" + mid)
    branch = commit_subj = ""
    commit_time = dirty = last_edit = 0
    if os.path.isdir(wt):
        branch = git(wt, "rev-parse", "--abbrev-ref", "HEAD")
        line = git(wt, "log", "-1", "--format=%ct%x1f%s")
        if "\x1f" in line:
            t, commit_subj = line.split("\x1f", 1)
            commit_time = int(t or 0)
        files = [l[3:].strip().strip('"') for l in git(wt, "status", "--porcelain").splitlines() if l.strip()]
        dirty = len(files)
        for f in files[:200]:
            try:
                last_edit = max(last_edit, int(os.path.getmtime(os.path.join(wt, f.split(" -> ")[-1]))))
            except OSError:
                pass

    task = None
    for p in glob.glob(os.path.join(TEAM, "claims", "*.json")):
        try:
            with open(p, encoding="utf-8-sig") as f:
                c = json.load(f)
        except Exception:
            continue
        if c.get("vem") == mid and c.get("status") not in ("klar", "done", "slappt", "släppt"):
            t = parse_time(c.get("tid", ""))
            if not task or t > task["time"]:
                task = {"id": c.get("id", ""), "text": c.get("beskrivning", ""), "pr": c.get("pr"),
                        "status": c.get("status", ""), "time": t}

    state = st.get("tillstand", "")
    running = state == "kör" and pid_alive(st.get("pid"))
    return {
        "id": mid, "terminal": "kör" if running else (state or "okänd"),
        "last_session": last_session, "branch": "" if branch == "HEAD" else branch,
        "dirty": dirty, "last_edit": last_edit, "commit": commit_subj[:140], "commit_time": commit_time,
        "task": task,
    }


def upload(data):
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    code, out = run(["gh", "api", f"repos/{REPO}/contents/{PATH}?ref={BRANCH}", "--jq", ".sha"])
    sha = out if code == 0 else ""
    payload = {"message": "Update live status", "branch": BRANCH,
               "content": base64.b64encode(body.encode("utf-8")).decode("ascii")}
    if sha:
        payload["sha"] = sha
    code, out = run(["gh", "api", "-X", "PUT", f"repos/{REPO}/contents/{PATH}", "--input", "-"],
                    inp=json.dumps(payload))
    return code == 0, out[:200]


def ensure_branch():
    code, _ = run(["gh", "api", f"repos/{REPO}/branches/{BRANCH}"])
    if code == 0:
        return
    _, sha = run(["gh", "api", f"repos/{REPO}/git/ref/heads/main", "--jq", ".object.sha"])
    run(["gh", "api", "-X", "POST", f"repos/{REPO}/git/refs", "-f", f"ref=refs/heads/{BRANCH}", "-f", f"sha={sha}"])


_last_full = [0]


def once():
    local = {"time": int(time.time()), "members": [member_status(m) for m in MEMBERS]}
    code, token = run(["gh", "auth", "token"])
    os.environ["GITHUB_TOKEN"] = token
    import build
    importlib.reload(build)  # picks up edits to build.py / team.json without a restart
    fast = time.time() - _last_full[0] < FULL_EVERY
    data = build.main(fast=fast, local=local)
    if not fast:
        _last_full[0] = time.time()
    ok, info = upload(data)
    stamp = datetime.now().strftime("%H:%M:%S")
    summary = ", ".join(f"{m['id']}: {m['terminal']}" for m in local["members"])
    summary += " | " + ", ".join(f"{p['name']} {p['milestones']['percent']}%" for p in data["projects"])
    print(f"{stamp} {'skickat' if ok else 'FEL: ' + info} ({summary})", flush=True)


def main():
    ensure_branch()
    if "--once" in sys.argv:
        once()
        return
    print("Rapporterar terminalernas status till teamsidan var 2:a minut. Ctrl+C stänger.", flush=True)
    while True:
        try:
            once()
        except Exception as e:
            print("fel:", e, flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
