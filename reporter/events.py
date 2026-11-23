"""The live feed: new commits by our agents, PRs opened and PRs merged, newest first.

Commits come from the shared Leonida repository on this PC (one `git for-each-ref` over every branch; a branch whose
head moved since the last look is a new commit). PRs come from one GitHub REST call for the most recently updated
PRs, at most once a minute. The feed keeps the last MAX events in STATE between runs.
"""
import json
import os
import subprocess
import time
from datetime import datetime

import agents

REPO_DIR = r"Z:\Claude\Leonida"
UPSTREAM = "ItsNotRexed/Leonida"
FORK = "evilclanker9000/Leonida"
ACCOUNT = "evilclanker9000"
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "events_state.json")
MAX = 120
PR_EVERY = 60
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(args, timeout=60):
    try:
        p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=NOWIN)
        return p.returncode, p.stdout
    except Exception:
        return 1, ""


def who(branch):
    a = agents.agent_of(branch)
    if not a:
        return "Claude" if branch.split("/")[0] in ("fix", "test", "feat", "fuzz", "docs") else ""
    return agents.NAMES.get(a) or ("DeepSeek " + a[8:] if a.startswith("deepseek") else a[0].upper() + a[1:])


def load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"heads": {}, "prs": {}, "events": [], "pr_checked": 0}


def save(state):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, STATE)


def commit_events(state, first):
    code, out = _run(["git", "-C", REPO_DIR, "for-each-ref", "refs/heads",
                      "--format=%(refname:short)%09%(objectname)%09%(committerdate:unix)%09%(subject)"])
    if code != 0:
        return []
    heads, new = state["heads"], []
    for line in out.splitlines():
        parts = line.split("\t", 3)
        if len(parts) < 4:
            continue
        branch, sha, t, subject = parts
        name = who(branch)
        if not name:
            continue
        old = heads.get(branch)
        heads[branch] = sha
        if old == sha or (first and int(t or 0) < time.time() - 3 * 3600):
            continue
        n = 1
        if old:
            c, cnt = _run(["git", "-C", REPO_DIR, "rev-list", "--count", f"{old}..{sha}"], timeout=20)
            n = int(cnt.strip() or 1) if c == 0 else 1
            if n == 0:  # a reset or rebase onto older history: not new work
                continue
        new.append({"t": int(t or time.time()), "agent": name, "kind": "commit", "n": n,
                    "title": subject[:160], "branch": branch,
                    "url": f"https://github.com/{FORK}/commit/{sha}", "id": "c" + sha})
    return new


def pr_events(state):
    if time.time() - state.get("pr_checked", 0) < PR_EVERY:
        return []
    state["pr_checked"] = int(time.time())
    code, out = _run(["gh", "api", f"repos/{UPSTREAM}/pulls?state=all&sort=updated&direction=desc&per_page=100"])
    if code != 0:
        return []
    try:
        pulls = json.loads(out)
    except Exception:
        return []
    seen, new = state["prs"], []
    for p in pulls:
        if (p.get("user") or {}).get("login", "").lower() != ACCOUNT:
            continue
        num = str(p["number"])
        rec = seen.setdefault(num, {})
        name = who(p["head"]["ref"]) or "?"
        base = {"agent": name, "title": p["title"][:160], "branch": p["head"]["ref"], "url": p["html_url"], "pr": p["number"]}
        if not rec.get("opened"):
            rec["opened"] = 1
            t = int(datetime.fromisoformat(p["created_at"].replace("Z", "+00:00")).timestamp())
            if t > time.time() - 3 * 3600:
                new.append({**base, "t": t, "kind": "pr", "id": "o" + num})
        if p.get("merged_at") and not rec.get("merged"):
            rec["merged"] = 1
            t = int(datetime.fromisoformat(p["merged_at"].replace("Z", "+00:00")).timestamp())
            c, cnt = _run(["gh", "api", f"repos/{UPSTREAM}/pulls/{num}", "--jq", ".commits"], timeout=30)
            n = int(cnt.strip() or 0) if c == 0 and cnt.strip().isdigit() else 0
            if t > time.time() - 3 * 3600:
                new.append({**base, "t": t, "kind": "merge", "n": n, "id": "m" + num})
    if len(seen) > 2000:  # keep the newest numbers only
        for k in sorted(seen, key=int)[:-1500]:
            del seen[k]
    return new


SCORE_EVERY = 300
TODAY_Q = """query($q:String!,$after:String){search(query:$q,type:ISSUE,first:100,after:$after){
  issueCount pageInfo{hasNextPage endCursor} nodes{... on PullRequest{commits{totalCount}}}}}"""


def _merged_today(login, day0_iso):
    total, after = 0, None
    for _ in range(10):
        args = ["gh", "api", "graphql", "-f", f"query={TODAY_Q}",
                "-f", f"q=repo:{UPSTREAM} is:pr is:merged author:{login} merged:>={day0_iso}"]
        if after:
            args += ["-f", f"after={after}"]
        code, out = _run(args)
        if code != 0:
            return None
        d = json.loads(out)["data"]["search"]
        total += sum(n["commits"]["totalCount"] for n in d["nodes"] if n)
        if not d["pageInfo"]["hasNextPage"]:
            break
        after = d["pageInfo"]["endCursor"]
    return total


def score(state):
    """What the contributor list counts: commits on main for us and the account above us, at most every 5 min."""
    old = state.get("score") or {}
    if time.time() - old.get("time", 0) < SCORE_EVERY:
        return old
    code, out = _run(["gh", "api", f"repos/{UPSTREAM}/contributors?per_page=10"])
    if code != 0:
        return old
    rows = [(c["login"], c["contributions"]) for c in json.loads(out) if c.get("type") == "User"]
    owner = UPSTREAM.split("/")[0].lower()
    others = [r for r in rows if r[0].lower() != owner]
    us = next((n for l, n in others if l.lower() == ACCOUNT), 0)
    rank = 1 + sum(1 for l, n in others if n > us)
    above = [r for r in others if r[1] > us]
    them = above[-1] if above else (next((r for r in others if r[0].lower() != ACCOUNT), None))
    day0 = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    day0_iso = day0.isoformat(timespec="seconds")
    code, out = _run(["gh", "api", f"search/issues?q=repo:{UPSTREAM}+is:pr+is:open+author:{ACCOUNT}&per_page=1",
                      "--jq", ".total_count"])
    s = {"time": int(time.time()), "us": us, "rank": rank, "rank_incl_owner": rank + 1,
         "them": them[0] if them else None, "them_n": them[1] if them else 0,
         "gap": (them[1] - us) if them and them[1] > us else 0,
         "us_today": _merged_today(ACCOUNT, day0_iso),
         "them_today": _merged_today(them[0], day0_iso) if them else None,
         "open_prs": int(out.strip()) if code == 0 and out.strip().isdigit() else None}
    state["score"] = s
    return s


def collect():
    state = load()
    first = not state["heads"]
    new = commit_events(state, first) + pr_events(state)
    ids = {e["id"] for e in state["events"]}
    events = state["events"] + [e for e in new if e["id"] not in ids]
    events.sort(key=lambda e: -e["t"])
    state["events"] = events[:MAX]
    try:
        score(state)
    except Exception as e:
        print("score:", e)
    save(state)
    return state["events"]


if __name__ == "__main__":
    t0 = time.time()
    ev = collect()
    for e in ev[:15]:
        print(datetime.fromtimestamp(e["t"]).strftime("%H:%M:%S"), e["agent"], e["kind"], e.get("n", ""), e["title"][:70])
    print(len(ev), f"{time.time() - t0:.1f}s")
