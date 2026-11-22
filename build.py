"""Builds site/data.json for the team panel from GitHub's public API.

Usage: GITHUB_TOKEN=... python build.py
Reads team.json (members and projects). Only public GitHub data is used.
"""
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = os.environ.get("GITHUB_TOKEN", "")
DAYS = 14

X2W_MILESTONES = [
    ("CPU: leaf-funktion översatt", 1, "present", r"leaf function"),
    ("CPU: direktanrop", 1, "present", r"direct call"),
    ("CPU: DLL-importer (namn + ordinal)", 1, "present", r"by ordinal"),
    ("Laddar image utan LoadLibrary", 1, "present", r"without LoadLibrary"),
    ("SEH och TLS-callbacks", 1, "present", r"TLS-callback"),
    ("Bink-allokering omkompilerad", 1, "present", r"Bink allocation"),
    ("Pixel-shaders till DXIL", 1, "present", r"desktop DXIL"),
    ("BC7-texturer på GPU", 1, "present", r"recovered mip levels"),
    ("FidelityFX-uppskalare", 1, "present", r"RCAS"),
    ("Skripttillstånd återskapade", 1, "present", r"state-tree"),
    ("GPU-täckning från communityn", 1, "present", r"Radeon GPU models passed"),
    ("Spelets CPU-kod körs", 5, "absent", r"Title CPU code execution[^.]*unverified"),
    ("Hela spelet startar", 8, "absent", r"full-game startup[^.]*unverified"),
    ("Bildfrekvens mätt", 4, "absent", r"frame rate[^.]*unverified"),
]
LEONIDA_WEIGHTS = [(r"^Booting", 10), (r"^PE loader", 3), (r"D3D12 .* Vulkan", 3)]
GOALS = {
    "status_table": "Procenten räknas från tabellen \"Project status\" i README:n: ✅ räknas som klart, ⚠️ som "
                    "halvvägs, ? som en fjärdedel och ❌ som inte påbörjat. Uppstart och spelbarhet väger 10 poäng, "
                    "PE-laddaren och D3D12 → Vulkan 3 poäng, övriga rader 1 poäng. Dessutom räknas uppstartsstegen "
                    "M0, M1 och så vidare i docs/d3d12_vulkan.md (2 poäng var: Met = klart, delvis met = halvvägs), "
                    "så procenten rör sig varje gång ett nytt steg klaras. Räknas om varannan minut.",
    "x2w": "Procenten räknas från README:n: varje delresultat som står där som verifierat räknas som klart. "
           "De tre sista stegen (CPU-koden körs, spelet startar, bildfrekvens mätt) väger 5, 8 och 4 poäng, "
           "övriga 1 poäng.",
}


def api(path, raw=False):
    url = path if path.startswith("http") else "https://api.github.com/" + path.lstrip("/")
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "teampanel-build"})
    if TOKEN:
        req.add_header("Authorization", "Bearer " + TOKEN)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
                link = r.headers.get("Link", "")
            return (body if raw else json.loads(body)), link
        except urllib.error.HTTPError as e:
            if e.code in (502, 503, 504) or (e.code == 403 and attempt < 3):
                time.sleep(2 * (attempt + 1))
                continue
            raise
    raise RuntimeError("GitHub API failed: " + url)


def paged(path, max_pages=40):
    out, url = [], path
    for _ in range(max_pages):
        data, link = api(url)
        out += data
        m = re.search(r'<([^>]+)>;\s*rel="next"', link or "")
        if not m:
            break
        url = m.group(1)
    return out


def ts(s):
    if not s:
        return 0
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def graphql(query, variables):
    req = urllib.request.Request("https://api.github.com/graphql",
                                 data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "User-Agent": "teampanel-build",
                                          "Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.loads(r.read())
    if out.get("errors"):
        raise RuntimeError(str(out["errors"])[:300])
    return out["data"]


OURS_Q = """query($q:String!,$after:String){search(query:$q,type:ISSUE,first:100,after:$after){
  pageInfo{hasNextPage endCursor}
  nodes{... on PullRequest{number title url headRefName state isDraft createdAt mergedAt updatedAt}}}}"""


def our_prs(repo, account):
    """All PRs by the account in the repo, with branch names, via one or a few GraphQL searches."""
    out, after = [], None
    for _ in range(10):
        d = graphql(OURS_Q, {"q": f"repo:{repo} is:pr author:{account} sort:created-desc", "after": after})["search"]
        for n in d["nodes"]:
            if not n:
                continue
            out.append({"number": n["number"], "title": n["title"], "html_url": n["url"],
                        "head": {"ref": n["headRefName"]}, "draft": n["isDraft"],
                        "state": "open" if n["state"] == "OPEN" else "closed",
                        "merged_at": n["mergedAt"], "created_at": n["createdAt"], "updated_at": n["updatedAt"]})
        if not d["pageInfo"]["hasNextPage"]:
            break
        after = d["pageInfo"]["endCursor"]
    return out


def boot_milestones(repo, ref):
    """Boot milestones (M0, M1, ...) from docs/d3d12_vulkan.md: Met = done, partly met = halfway."""
    try:
        b, _ = api(f"repos/{repo}/contents/docs/d3d12_vulkan.md?ref={ref}")
        doc = base64.b64decode(b.get("content", "")).decode("utf-8", "replace")
    except Exception:
        return []
    items = []
    for line in doc.splitlines():
        m = re.match(r"\|\s*\*\*(M[\d.]+)\*\*\s*\|", line)
        if not m:
            continue
        done_col = line.rstrip().rstrip("|").split("|")[-1]
        low = done_col.lower()
        if re.search(r"\*\*met\b", low) and "still open" not in low and "part met" not in low and "stand-in" not in low:
            score, state = 1.0, "done"
        elif "met" in low:
            score, state = 0.5, "partial"
        else:
            score, state = 0.0, "todo"
        items.append({"name": f"Uppstart {m.group(1)} (D3D12 → Vulkan-planen)", "weight": 2, "state": state, "score": score})
    return items


def milestones(kind, readme, extra=None):
    items = []
    if kind == "status_table":
        m = re.search(r"^##\s*Project status\s*$(.*?)^##\s", readme, re.M | re.S)
        for line in (m.group(1) if m else "").splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 2 or cells[0] in ("What", "") or set(cells[0]) <= set("-: "):
                continue
            name, status = re.sub(r"[*`]", "", cells[0]), cells[1]
            if status.startswith("✅"):
                score, state = 1.0, "done"
            elif status.startswith("⚠"):
                score, state = 0.5, "partial"
            elif status.startswith("?"):
                score, state = 0.25, "partial"
            else:
                score, state = 0.0, "todo"
            weight = next((w for pat, w in LEONIDA_WEIGHTS if re.search(pat, name)), 1)
            items.append({"name": name, "weight": weight, "state": state, "score": score})
    else:
        for name, weight, mode, pattern in X2W_MILESTONES:
            found = bool(re.search(pattern, readme, re.I | re.S))
            done = found if mode == "present" else (bool(readme) and not found)
            items.append({"name": name, "weight": weight, "state": "done" if done else "todo",
                          "score": 1.0 if done else 0.0})
    items += extra or []
    tw = sum(i["weight"] for i in items)
    dw = sum(i["weight"] * i["score"] for i in items)
    return {"items": items, "percent": round(100 * dw / tw) if tw else 0,
            "done": sum(i["state"] == "done" for i in items), "total": len(items), "goal": GOALS.get(kind, "")}


def rank(rows, owner, members):
    rows.sort(key=lambda r: (-r["n"], r["login"].lower()))
    mem = {m.lower() for m in members}
    for i, r in enumerate(rows):
        r["rank"] = 1 + sum(1 for x in rows if x["n"] > r["n"])  # ties share a place
        r["owner"] = r["login"].lower() == owner.lower()
        r["member"] = r["login"].lower() in mem
    others = [r for r in rows if not r["owner"]]
    team_n = sum(r["n"] for r in rows if r["member"])
    team_rank = 1 + sum(1 for r in others if not r["member"] and r["n"] > team_n)
    best = next((r for r in others if r["member"]), None)
    nxt = None
    if best:
        better = [r for r in others if r["n"] > best["n"]]
        if better:
            nxt = {"login": better[-1]["login"], "gap": better[-1]["n"] - best["n"] + 1}
    top = rows[:10] + [r for r in rows[10:] if r["member"]]
    return {"top": top, "total": len(others), "team_n": team_n, "team_rank_ex_owner": team_rank,
            "best_member": best,
            "best_rank_ex_owner": (1 + sum(1 for r in others if r["n"] > best["n"])) if best else None, "next": nxt}


def member_for(ref, members):
    """Which team member owns a branch, by prefix (`name/...` or the member's own prefixes)."""
    r = (ref or "").lower()
    for m in members:
        for pre in [m["name"].lower() + "/"] + [x.lower() for x in m.get("prefixes", [])]:
            if r.startswith(pre):
                return m["name"]
    return next((m["name"] for m in members if m.get("default")), members[0]["name"])


def push_events(fork, members):
    """Recent pushes to the fork, per member (GitHub keeps about 90 days / 300 events)."""
    try:
        events = paged(f"repos/{fork}/events?per_page=100", max_pages=3)
    except Exception:
        return []
    out = []
    for e in events:
        if e.get("type") != "PushEvent":
            continue
        ref = (e.get("payload") or {}).get("ref", "").replace("refs/heads/", "")
        out.append({"time": ts(e["created_at"]), "branch": ref, "who": member_for(ref, members),
                    "commits": len((e.get("payload") or {}).get("commits") or []) or (e.get("payload") or {}).get("size", 0)})
    return out


def build_project(p, cfg, prev=None):
    members = cfg["members"]
    account = cfg["account"]
    ignore = {x.lower() for x in cfg.get("ignore", [])}
    counted = lambda l: bool(l) and l.lower() not in ignore and not l.lower().endswith("[bot]")
    repo, owner = p["repo"], p["repo"].split("/")[0]
    now = time.time()
    day0 = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    meta, _ = api(f"repos/{repo}")
    readme_b, _ = api(f"repos/{repo}/contents/README.md?ref={meta['default_branch']}")
    readme = base64.b64decode(readme_b.get("content", "")).decode("utf-8", "replace")

    if prev:
        leader, account_commits = prev["leader"], prev["team"]["commits"]
    else:
        contrib = paged(f"repos/{repo}/contributors?per_page=100", max_pages=5)
        commit_rows = [{"login": c["login"], "n": c["contributions"]} for c in contrib
                       if c.get("type") == "User" and counted(c.get("login"))]
        account_commits = next((r["n"] for r in commit_rows if r["login"].lower() == account.lower()), 0)
        pulls = paged(f"repos/{repo}/pulls?state=all&per_page=100&sort=created&direction=desc", max_pages=40)
        by_author = {}
        for pr in pulls:
            login = (pr.get("user") or {}).get("login", "")
            if not counted(login):
                continue
            a = by_author.setdefault(login, {"login": login, "n": 0, "opened": 0})
            a["opened"] += 1
            if pr.get("merged_at"):
                a["n"] += 1
        leader = {"commits": rank(commit_rows, owner, [account]), "prs": rank(list(by_author.values()), owner, [account])}
    ours = our_prs(repo, account)

    pushes = push_events(p["fork"], members)
    state = lambda pr: ("DRAFT" if pr["state"] == "open" and pr.get("draft") else
                        "MERGED" if pr.get("merged_at") else "OPEN" if pr["state"] == "open" else "CLOSED")
    people, feed = [], []
    for m in members:
        name = m["name"]
        prs = [pr for pr in ours if member_for(pr["head"]["ref"], members) == name]
        mp = [e for e in pushes if e["who"] == name]
        days = []
        for i in range(DAYS - 1, -1, -1):
            start = day0 - i * 86400
            days.append({"start": int(start),
                         "opened": sum(1 for pr in prs if start <= ts(pr["created_at"]) < start + 86400),
                         "merged": sum(1 for pr in prs if start <= ts(pr.get("merged_at")) < start + 86400)})
        recent = sorted(prs, key=lambda pr: -ts(pr["updated_at"]))[:6]
        last_pr = max([max(ts(pr["created_at"]), ts(pr.get("merged_at"))) for pr in prs] + [0])
        people.append({
            "name": name, "opened": len(prs),
            "merged": sum(1 for pr in prs if pr.get("merged_at")),
            "open": sum(1 for pr in prs if pr["state"] == "open"),
            "closed": sum(1 for pr in prs if pr["state"] == "closed" and not pr.get("merged_at")),
            "last_push": max([e["time"] for e in mp] + [0]),
            "last_branch": (max(mp, key=lambda e: e["time"])["branch"] if mp else ""),
            "last_pr": last_pr, "days": days,
            "recent": [{"number": pr["number"], "title": pr["title"], "url": pr["html_url"],
                        "state": state(pr), "branch": pr["head"]["ref"], "updated": ts(pr["updated_at"])}
                       for pr in recent],
        })
        for pr in prs:
            if now - ts(pr["created_at"]) < 7 * 86400:
                feed.append({"time": ts(pr["created_at"]), "who": name, "kind": "pr", "text": pr["title"],
                             "meta": f"PR #{pr['number']} öppnad", "url": pr["html_url"]})
            if pr.get("merged_at") and now - ts(pr["merged_at"]) < 7 * 86400:
                feed.append({"time": ts(pr["merged_at"]), "who": name, "kind": "merge", "text": pr["title"],
                             "meta": f"PR #{pr['number']} mergad", "url": pr["html_url"]})
    feed.sort(key=lambda e: -e["time"])
    return {
        "id": p["id"], "name": p["name"], "repo": repo, "fork": p["fork"], "owner": owner,
        "milestones": milestones(p["milestones"], readme,
                                 boot_milestones(repo, meta["default_branch"]) if p["milestones"] == "status_table" else None),
        "people": people, "pushes": pushes[:200],
        "team": {"commits": account_commits, **{k: sum(x[k] for x in people) for k in ("opened", "merged", "open", "closed")}},
        "leader": leader, "leader_built": prev.get("leader_built", int(now)) if prev else int(now),
        "feed": feed[:40],
        # compact list of our PRs so the browser can recompute stats with live data
        "ours": [{"n": pr["number"], "t": pr["title"], "u": pr["html_url"], "r": pr["head"]["ref"],
                  "s": state(pr), "c": ts(pr["created_at"]), "m": ts(pr.get("merged_at")),
                  "up": ts(pr["updated_at"])} for pr in ours],
    }


LEADER_MAX_AGE = 15 * 60


def main(fast=False, local=None):
    """fast=True reuses a leaderboard younger than 15 minutes from the last build."""
    with open(os.path.join(HERE, "team.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    prev = {}
    if fast:
        try:
            with open(os.path.join(HERE, "site", "data.json"), encoding="utf-8") as f:
                old = json.load(f)
            prev = {x["id"]: x for x in old.get("projects", [])
                    if "leader" in x and time.time() - x.get("leader_built", 0) < LEADER_MAX_AGE}
        except Exception:
            prev = {}
    out = {"title": cfg.get("title", "Team"), "built": int(time.time()), "account": cfg["account"],
           "members": [{"name": m["name"], "prefixes": [m["name"].lower() + "/"] + m.get("prefixes", []),
                        "default": bool(m.get("default"))} for m in cfg["members"]],
           "quiet_minutes": cfg.get("quiet_minutes", 60), "stopped_minutes": cfg.get("stopped_minutes", 180),
           "projects": [build_project(p, cfg, prev.get(p["id"])) for p in cfg["projects"]]}
    if local:
        out["local"] = local
    site = os.path.join(HERE, "site")
    os.makedirs(site, exist_ok=True)
    with open(os.path.join(site, "data.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("ok", {p["id"]: p["team"] for p in out["projects"]})
    return out


if __name__ == "__main__":
    main()
