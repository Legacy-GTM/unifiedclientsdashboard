#!/usr/bin/env python3
"""Combined client dashboard - Instantly (Dave Lorenzo workspace; 'ESL' campaigns = Exit Success Lab) + Sendkit (one workspace = one client).
Key metrics (Harvey): sends/day (today, yesterday, 14-day trend), leads contacted, net new leads left to contact (active campaigns), leads generated.
Usage: python3 refresh.py [--png] [--publish]   (env: INSTANTLY_API_KEY, SENDKIT_API_KEY)"""
import os, sys, json, html, time, datetime, urllib.request, urllib.parse, urllib.error
OUT = os.environ.get("DASH_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
LAST_GOOD = os.path.join(OUT, "last_good.json")
# Sendkit workspaces excluded from the dashboard (not fetched, not in totals). Case-insensitive.
SKIP_SK = {"test", "profitable tradie pilot", "avalanche pilot", "midcrunch (archived)", "leader nexus"}
DAYS = 14
TODAY = datetime.date.today()
DATES = [(TODAY - datetime.timedelta(days=i)).isoformat() for i in range(DAYS - 1, -1, -1)]

# ---- METRICS CONFIG: (label, function(client totals) -> display). Edit to change the main table / cards. ----
def n(v): return "n/a" if v is None else f"{v:,}"
METRICS = [
    ("Sent today",            lambda t: n(t["daily"].get(DATES[-1], 0))),
    ("Sent yesterday",        lambda t: n(t["daily"].get(DATES[-2], 0))),
    (f"Avg sent/day ({DAYS}d)", lambda t: n(round(sum(t["daily"].get(d, 0) for d in DATES) / DAYS))),
    ("Leads contacted",       lambda t: n(t["contacted"])),
    ("Leads left to contact", lambda t: n(t["left"])),
    ("Leads gen. today",      lambda t: n(t.get("daily_pos", {}).get(DATES[-1], 0))),
    ("Leads gen. yesterday",  lambda t: n(t.get("daily_pos", {}).get(DATES[-2], 0))),
    ("Leads generated",       lambda t: n(t["positive"])),
]
SECONDARY = [("Campaigns", "n"), ("Active", "active"), ("Emails sent (all time)", "sent"), ("Human replies", "replies"), ("Bounces", "bounces")]

def get(url, headers):
    for i in range(6):
        try:
            req = urllib.request.Request(url, headers={**headers, "User-Agent": "dashboard/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r: return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(5 * (i + 1)); continue
            body = e.read().decode("utf-8", "replace")[:300]
            raise urllib.error.HTTPError(e.url, e.code, f"{e.reason} | {body}", e.headers, None) from None
    raise RuntimeError("rate limited " + url)

def new_client(platform, name): return dict(platform=platform, client=name, campaigns=[], daily={})

def post(url, headers, body):
    for i in range(6):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                         headers={**headers, "Content-Type": "application/json", "User-Agent": "dashboard/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r: return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(5 * (i + 1)); continue
            body_ = e.read().decode("utf-8", "replace")[:300]
            raise urllib.error.HTTPError(e.url, e.code, f"{e.reason} | {body_}", e.headers, None) from None
    raise RuntimeError("rate limited " + url)

def instantly_leads(IB, h, cid, flt):
    """All leads in a campaign matching an Instantly filter (FILTER_VAL_SKIPPED, FILTER_VAL_NOT_CONTACTED, ...)."""
    out, cur = [], None
    while True:
        b = {"campaign": cid, "filter": flt, "limit": 100}
        if cur: b["starting_after"] = cur
        d = post(IB + "/leads/list", h, b); it = d.get("items", []); out += it
        cur = d.get("next_starting_after")
        if not cur or not it: return out

def instantly():
    IB = "https://api.instantly.ai/api/v2"; h = {"Authorization": "Bearer " + os.environ["INSTANTLY_API_KEY"]}
    # Harvey: every campaign in Dave's Instantly workspace is Exit Success Lab
    cl = new_client("Instantly", "Exit Success Lab (Dave Lorenzo)")
    for c in get(IB + "/campaigns/analytics", h):
        active = c.get("campaign_status") == 1
        bounced = c.get("bounced_count", 0)
        # Skipped leads (Instantly lead status -3) are excluded everywhere. Analytics has no skipped field, so count them
        # from the leads list. Skipped leads are never emailed, so they are not in contacted, sends, or opportunities.
        skipped, left = 0, 0
        if active:
            skipped = len(instantly_leads(IB, h, c["campaign_id"], "FILTER_VAL_SKIPPED"))
            # Net new left = not yet contacted AND still queued (status 1). Excludes skipped (-3), bounced (-1),
            # unsubscribed (-2), paused (2) and completed (3) leads.
            left = sum(1 for x in instantly_leads(IB, h, c["campaign_id"], "FILTER_VAL_NOT_CONTACTED") if x.get("status") == 1)
        cl["campaigns"].append(dict(id=c["campaign_id"], campaign=c["campaign_name"], active=active, sent=c.get("emails_sent_count", 0),
            contacted=c.get("new_leads_contacted_count", 0), left=left, replies=c.get("reply_count_unique", 0),
            positive=c.get("total_opportunities", 0), bounces=bounced, total_leads=c.get("leads_count", 0), skipped=skipped))
    q = urllib.parse.urlencode({"start_date": DATES[0], "end_date": DATES[-1]})
    rows = get(f"{IB}/campaigns/analytics/daily?{q}", h)
    cl["daily"] = {d["date"]: d["sent"] for d in rows}
    cl["daily_pos"] = {d["date"]: d.get("unique_opportunities", d.get("opportunities", 0)) for d in rows}
    return [cl]

def sendkit():
    SB = "https://api.sendkit.ai/v1"; h = {"Authorization": "Bearer " + os.environ["SENDKIT_API_KEY"]}
    out = []
    for w in get(SB + "/workspaces", h)["data"]:
        if w["name"].lower() in SKIP_SK: continue
        hw = {**h, "X-Workspace-Id": w["id"]}; c0 = new_client("Sendkit", w["name"]); cursor = None
        while True:
            q = {"limit": 100}
            if cursor: q["cursor"] = cursor
            d = get(SB + "/campaigns?" + urllib.parse.urlencode(q), hw)
            for c in d["data"]:
                s = c.get("stats") or {}; active = c.get("status") == "active"
                tot = s.get("totalLeads", 0); contacted = s.get("contactedLeads", 0); bounced = s.get("bounced", 0)
                skipped, left = 0, 0
                if active:
                    # statusBreakdown (campaign analytics) has explicit skipped + pending counts.
                    # Net new left = pending only (queued, never contacted); skipped leads are excluded.
                    sb = (get(f"{SB}/campaigns/{c['_id']}/analytics", hw).get("data") or {}).get("statusBreakdown") or {}
                    skipped = sb.get("skipped", 0); left = sb.get("pending", s.get("pending", 0))
                else:
                    skipped = max(0, tot - (s.get("pending", 0) + s.get("active", 0) + bounced + s.get("replied", 0)
                                            + s.get("completed", 0) + s.get("failed", 0) + s.get("unsubscribed", 0)))
                c0["campaigns"].append(dict(id=c["_id"], campaign=c["name"], active=active, sent=s.get("sent", 0),
                    contacted=contacted, left=left, replies=s.get("replied", 0),
                    positive=s.get("positiveReplied", 0), bounces=bounced, total_leads=tot, skipped=skipped))
            p = d.get("pagination") or {}
            if not p.get("hasMore") or not p.get("nextCursor") or p.get("nextCursor") == cursor: break
            cursor = p["nextCursor"]
        rows = get(SB + "/analytics/daily", hw)["data"]  # last ~30 days
        c0["daily"] = {x["_id"]: x.get("sent", 0) for x in rows}
        # Prefer positiveReplied; never fall back to sent. Cap absurd spikes vs campaign totals.
        pos_total = sum(c.get("positive", 0) for c in c0["campaigns"])
        daily_pos = {}
        for x in rows:
            v = x.get("positiveReplied")
            if not isinstance(v, (int, float)) or v < 0:
                v = 0
            daily_pos[x["_id"]] = int(v)
        window = sum(daily_pos.get(d, 0) for d in DATES)
        # Guard: a 14d window should not dwarf all-time positive replies (API glitches have returned sent-scale numbers).
        if pos_total > 0 and window > max(50, pos_total * 3):
            raise RuntimeError(f"{w['name']}: daily positiveReplied window={window} vs campaign positive={pos_total} (rejecting)")
        c0["daily_pos"] = daily_pos
        out.append(c0)
    return out

def totals(clients):
    cs = [c for x in clients for c in x["campaigns"]]
    t = {k: sum(c[k] for c in cs) for k in ("sent", "contacted", "left", "replies", "positive", "bounces")}
    t["n"] = len(cs); t["active"] = sum(c["active"] for c in cs)
    t["daily"] = {d: sum(x["daily"].get(d, 0) for x in clients) for d in DATES}
    t["daily_pos"] = {d: sum(x.get("daily_pos", {}).get(d, 0) for x in clients) for d in DATES}
    return t

LOW_LEFT = 500          # flag (muted red) if a sending client has fewer active leads left than this
STRONG_GEN_RATE = 0.004 # flag (brand blue) if leads generated / contacted >= 0.4%
# Legacy GTM brand (from legacygtm.com CSS variables)
BRAND = dict(paper="#F2F1ED", paper2="#E9E8E2", card="#FFFFFF", ink="#0B1220", body="#4C5568", faint="#8A93A3",
             line="#DBDAD2", blue="#004EE5", blue_soft="#E3EAFB", red="#B42318")

def esc(x): return html.escape(str(x))

def bars(daily, w=300, h=64, color="var(--blue)"):
    vals = [daily.get(d, 0) for d in DATES]; m = max(vals) or 1; bw = w / len(vals); out = []
    for i, v in enumerate(vals):
        bh = max(1.5 if v else 0, (h - 14) * v / m)
        out.append(f'<rect class="bar" x="{i*bw+2:.1f}" y="{h-13-bh:.1f}" width="{bw-4:.1f}" height="{bh:.1f}" fill="{color}" data-tip="{DATES[i]} · {v:,}"></rect>')
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="chart"><line x1="0" x2="{w}" y1="{h-12.5}" y2="{h-12.5}" class="axl"/>{"".join(out)}'
            f'<text x="0" y="{h}" class="ax">{DATES[0][5:]}</text><text x="{w}" y="{h}" class="ax" text-anchor="end">{DATES[-1][5:]}</text></svg>')

def health(t):
    out = []
    if sum(t["daily"].get(d, 0) for d in DATES[-7:]) == 0: out.append(("mute", "Idle 7 days"))
    elif t["left"] < LOW_LEFT: out.append(("crit", f"Low lead supply · {t['left']:,} left"))
    rate = t["positive"] / t["contacted"] if t["contacted"] else 0
    if t["positive"] and rate >= STRONG_GEN_RATE: out.append(("good", f"Strong lead gen · {rate:.2%}"))
    return "".join(f'<span class="h {c}"><i></i>{esc(x)}</span>' for c, x in out) or '<span class="h mute"><i></i>On track</span>'

def pos_section(clients, T):
    """Positive replies by day (own section). Series = per-client daily_pos, skipped leads never included:
    Instantly = unique opportunities (leads marked positive/interested) per day; Sendkit = positiveReplied per day."""
    tot = [T["daily_pos"].get(d, 0) for d in DATES]; total = sum(tot); best = max(tot) if tot else 0
    best_d = DATES[tot.index(best)] if best else None
    stats = [("Positive replies, last %d days" % DAYS, n(total)), ("Today (partial)", n(tot[-1])), ("Yesterday", n(tot[-2])),
             ("Daily average", f"{total / DAYS:.1f}"), ("Best day", f"{best} · {best_d[5:]}" if best else "0")]
    kp = "".join(f'<div class="kpi"><div class="kl">{esc(l)}</div><div class="kv">{esc(v)}</div></div>' for l, v in stats)
    w, h = 1200, 150; m = best or 1; bw = w / DAYS; out = []
    for i, v in enumerate(tot):
        bh = max(2 if v else 0, (h - 40) * v / m); x = i * bw
        out.append(f'<rect class="bar" x="{x+8:.1f}" y="{h-18-bh:.1f}" width="{bw-16:.1f}" height="{bh:.1f}" data-tip="{DATES[i]} · {v:,} positive replies"></rect>'
                   f'<text x="{x+bw/2:.1f}" y="{h-18-bh-6:.1f}" class="ax pv" text-anchor="middle">{v}</text>'
                   f'<text x="{x+bw/2:.1f}" y="{h}" class="ax" text-anchor="middle">{DATES[i][5:]}</text>')
    chart = f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="chart pchart"><line x1="0" x2="{w}" y1="{h-17.5}" y2="{h-17.5}" class="axl"/>{"".join(out)}</svg>'
    head = "".join(f'<th class=num>{d[5:]}</th>' for d in DATES)
    rows = ""
    for x in sorted(clients, key=lambda x: -sum(x.get("daily_pos", {}).get(d, 0) for x_ in [0] for d in DATES)):
        v = [x.get("daily_pos", {}).get(d, 0) for d in DATES]
        cells = "".join(f'<td class="num{"" if k else " faint"}">{k}</td>' for k in v)
        rows += f'<tr><td>{esc(x["client"])} <span class="faint pf">{x["platform"]}</span></td>{cells}<td class="num"><b>{sum(v)}</b></td></tr>'
    foot = "<tr class='tot'><td>All clients</td>" + "".join(f"<td class=num>{k}</td>" for k in tot) + f"<td class=num>{total}</td></tr>"
    return (f'<h2>Positive replies by day</h2><div class="kpis">{kp}</div>'
            f'<div class="card pcard"><div class="cl" style="margin-top:0">Positive replies per day, all clients, last {DAYS} days</div>{chart}</div>'
            f'<div class="tw ptw"><table><thead><tr><th>Client</th>{head}<th class=num>Total</th></tr></thead><tbody>{rows}</tbody><tfoot>{foot}</tfoot></table></div>'
            f'<div class="foot" style="margin-top:14px">Counts positive replies on the day they were recorded. Instantly (Exit Success Lab): leads marked positive/interested (Instantly opportunities). '
            f'Sendkit: positive replies per workspace. Skipped leads are never emailed, so they are not included. Today is partial; Instantly days are UTC days.</div>')

def render(clients, errors):
    import base64
    now = datetime.datetime.now().strftime("%d %b %Y, %H:%M WAT")
    lp = os.path.join(OUT, "logo.webp")
    logo = f'<img class="logo" alt="Legacy GTM" src="data:image/webp;base64,{base64.b64encode(open(lp,"rb").read()).decode()}">' if os.path.exists(lp) else ""
    clients = sorted(clients, key=lambda x: -totals([x])["contacted"]); T = totals(clients)
    kpis = "".join(f'<div class="kpi"><div class="kl">{esc(l)}</div><div class="kv">{f(T)}</div></div>' for l, f in METRICS)
    cards = ""
    for x in clients:
        t = totals([x])
        rows = "".join(f'<div class="m"><span>{esc(l)}</span><b>{f(t)}</b></div>' for l, f in METRICS)
        cards += (f'<div class="card"><div class="ch"><div class="cn">{esc(x["client"])}</div><div class="pf">{x["platform"]}</div></div>'
                  f'<div class="hs">{health(t)}</div><div class="ms">{rows}</div>'
                  f'<div class="cl">Daily sends, last {DAYS} days</div>{bars(x["daily"])}'
                  f'<div class="cl">Leads generated, last {DAYS} days</div>{bars(x.get("daily_pos", {}), color="var(--ink)")}</div>')
    head = ["Client", "Platform"] + [l for l, _ in METRICS] + ["Status"]
    def raw(v):
        try: return float(str(v).replace(",", ""))
        except Exception: return str(v)
    trs = ""
    for x in clients:
        t = totals([x]); cells = [x["client"], x["platform"]] + [f(t) for _, f in METRICS]
        trs += "<tr>" + "".join(f'<td data-v="{esc(raw(c))}"{" class=num" if i > 1 else ""}>{esc(c)}</td>' for i, c in enumerate(cells)) + f'<td>{health(t)}</td></tr>'
    tot = "<tr class='tot'><td>All clients</td><td></td>" + "".join(f"<td class=num>{f(T)}</td>" for _, f in METRICS) + "<td></td></tr>"
    ths = "".join(f'<th onclick="sortT({i})"{" class=num" if 1 < i < len(head)-1 else ""}>{esc(h)}</th>' if i < len(head) - 1 else f"<th>{h}</th>" for i, h in enumerate(head))
    det = ""
    for x in clients:
        cs = sorted(x["campaigns"], key=lambda c: (not c["active"], -c["sent"]))
        det += f'<details><summary>{esc(x["client"])} <span>{len(cs)} campaigns</span></summary><div class="tw"><table><tr><th>Campaign</th><th>Status</th><th class=num>Sent</th><th class=num>Contacted</th><th class=num>Left</th><th class=num>Skipped (excl.)</th><th class=num>Leads gen.</th><th class=num>Replies</th><th class=num>Bounces</th></tr>' + "".join(
            f'<tr><td>{esc(c["campaign"])}</td><td>{"Active" if c["active"] else "<span class=faint>Inactive</span>"}</td><td class=num>{n(c["sent"])}</td><td class=num>{n(c["contacted"])}</td><td class=num>{n(c["left"])}</td><td class=num>{n(c.get("skipped", 0))}</td><td class=num>{n(c["positive"])}</td><td class=num>{n(c["replies"])}</td><td class=num>{n(c["bounces"])}</td></tr>' for c in cs) + "</table></div></details>"
    pos = pos_section(clients, T)
    err = "".join(f'<div class="err">{esc(e)}</div>' for e in errors)
    B = BRAND
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Legacy GTM Client Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700&family=Instrument+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{{--paper:{B['paper']};--paper2:{B['paper2']};--card:{B['card']};--ink:{B['ink']};--body:{B['body']};--faint:{B['faint']};--line:{B['line']};--blue:{B['blue']};--soft:{B['blue_soft']};--red:{B['red']}}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--body);font:15px/1.5 "Instrument Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
.wrap{{max-width:1280px;margin:0 auto;padding:48px 40px 80px}}
header{{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;padding-bottom:28px;border-bottom:1px solid var(--line);margin-bottom:40px}}
.brand{{display:flex;align-items:center;gap:16px}} .logo{{width:52px;height:52px;object-fit:contain;background:#fff;border:1px solid var(--line);border-radius:10px;padding:4px}}
.eyebrow,.kl,.cl,.pf,th,.stamp,summary span,.ax{{font-family:"JetBrains Mono",ui-monospace,monospace;text-transform:uppercase;letter-spacing:.08em;font-size:11px;color:var(--faint)}}
h1{{font-family:Archivo,Arial,sans-serif;font-weight:700;font-size:28px;letter-spacing:-.02em;color:var(--ink);margin:2px 0 0}}
h2{{font-family:Archivo,Arial,sans-serif;font-weight:600;font-size:18px;color:var(--ink);margin:56px 0 18px;letter-spacing:-.01em}}
.stamp b{{color:var(--ink);font-weight:500}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));border:1px solid var(--line);border-radius:12px;background:var(--card)}}
.kpi{{padding:22px 22px 24px;border-left:1px solid var(--line)}} .kpi:first-child{{border-left:0}}
.kv{{font-family:Archivo,Arial,sans-serif;font-weight:600;font-size:30px;color:var(--ink);letter-spacing:-.02em;margin-top:10px;font-variant-numeric:tabular-nums}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:20px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:24px}}
.ch{{display:flex;justify-content:space-between;align-items:baseline;gap:12px}} .cn{{font-family:Archivo,Arial,sans-serif;font-weight:600;font-size:16px;color:var(--ink)}}
.hs{{display:flex;flex-wrap:wrap;gap:4px 16px;margin:8px 0 18px;min-height:20px}}
.h{{font-size:12.5px;color:var(--body);display:inline-flex;align-items:center;gap:7px;white-space:nowrap}} .h i{{width:6px;height:6px;border-radius:50%;background:var(--faint)}}
.h.good i{{background:var(--blue)}} .h.crit{{color:var(--red)}} .h.crit i{{background:var(--red)}} .h.mute{{color:var(--faint)}}
.ms{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px 12px;padding:16px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}}
.m span{{display:block;font-size:12px;color:var(--faint)}} .m b{{font-weight:600;color:var(--ink);font-size:16px;font-variant-numeric:tabular-nums}}
.cl{{margin:16px 0 6px}} .chart{{width:100%;height:64px;overflow:visible}} .bar{{fill:var(--blue)}} .bar:hover{{fill:var(--ink);cursor:default}} .ax{{font-size:9px;fill:var(--faint)}} .axl{{stroke:var(--line)}}
.tw{{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:12px}} table{{width:100%;border-collapse:collapse;font-size:14px}}
th{{text-align:left;padding:14px 12px;letter-spacing:.04em;border-bottom:1px solid var(--line);cursor:pointer;user-select:none;white-space:normal;vertical-align:bottom;font-weight:500}}
#cmp td:last-child{{white-space:normal;min-width:190px}} #cmp td:last-child .h{{white-space:normal}} th:hover{{color:var(--ink)}}
td{{padding:13px 12px;border-bottom:1px solid var(--paper2);white-space:nowrap;color:var(--ink)}} .num{{text-align:right;font-variant-numeric:tabular-nums}} th.num{{text-align:right}}
tbody tr:hover td{{background:#FAFAF8}} tr.tot td{{font-weight:600;border-top:1px solid var(--line);border-bottom:0}} .faint{{color:var(--faint)}}
details{{border-top:1px solid var(--line);padding:4px 0}} details:last-of-type{{border-bottom:1px solid var(--line)}} summary{{cursor:pointer;padding:14px 0;color:var(--ink);font-weight:500;list-style:none}} summary span{{margin-left:10px}}
.pcard{{margin-top:20px}} .pchart{{height:150px}} .pv{{fill:var(--ink)}} .ptw{{margin-top:20px}} .ptw table{{font-size:13px}} .ptw td,.ptw th{{padding:10px 8px;white-space:nowrap}} .ptw td:first-child{{white-space:normal;min-width:200px}} .ptw td:not(:first-child){{font-family:"JetBrains Mono",monospace}}
details .tw{{margin:4px 0 18px}} details table{{font-size:13px}} details td,details th{{padding:9px 14px}}
.err{{border:1px solid var(--red);color:var(--red);padding:10px 14px;border-radius:8px;margin-bottom:20px}}
.foot{{margin-top:48px;font-size:12.5px;color:var(--faint);max-width:820px}}
#tip{{position:fixed;pointer-events:none;background:var(--ink);color:#fff;padding:5px 9px;border-radius:6px;font:12px "JetBrains Mono",monospace;display:none}}
@media(max-width:1000px){{.kpis{{grid-template-columns:repeat(3,1fr)}} .kpi:nth-child(4){{border-left:0}} .kpi:nth-child(n+4){{border-top:1px solid var(--line)}}}}
@media(max-width:600px){{.wrap{{padding:28px 18px}} .kpis{{grid-template-columns:1fr 1fr}} .kpi{{border-left:0;border-top:1px solid var(--line)}} .grid{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<header><div class="brand">{logo}<div><div class="eyebrow">Legacy GTM</div><h1>Client Dashboard</h1></div></div>
<div class="stamp">Last refreshed <b>{now}</b></div></header>{err}
<div class="kpis">{kpis}</div>
{pos}
<h2>Clients</h2><div class="grid">{cards}</div>
<h2>Comparison</h2><div class="tw"><table id="cmp"><thead><tr>{ths}</tr></thead><tbody>{trs}</tbody><tfoot>{tot}</tfoot></table></div>
<h2>Campaigns</h2>{det}
<div class="foot">Skipped leads are excluded from every number: they are never emailed, so they are not in sends, leads contacted or leads generated, and they are not counted as leads left. Sent today is partial; platform days are UTC. Leads left to contact (active campaigns only) = leads still queued that have never been contacted (Instantly: not contacted and active; Sendkit: pending), so skipped, bounced, unsubscribed and paused leads are left out. Leads gen. today/yesterday come from Instantly daily opportunities and Sendkit daily positiveReplied. Leads generated = Instantly opportunities and Sendkit positive replies. Red marks fewer than {LOW_LEFT:,} leads left while sending; blue marks lead generation at or above {STRONG_GEN_RATE:.1%} of contacted leads.</div></div>
<div id="tip"></div><script>
const tip=document.getElementById('tip');document.querySelectorAll('.bar').forEach(b=>{{b.addEventListener('mousemove',e=>{{tip.style.display='block';tip.textContent=b.dataset.tip;tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY-30)+'px'}});b.addEventListener('mouseleave',()=>tip.style.display='none')}});
let dir={{}};function sortT(i){{const tb=document.querySelector('#cmp tbody');const rows=[...tb.rows];dir[i]=!dir[i];
rows.sort((a,b)=>{{let x=a.cells[i].dataset.v,y=b.cells[i].dataset.v;const nx=parseFloat(x),ny=parseFloat(y);
if(!isNaN(nx)&&!isNaN(ny))return dir[i]?ny-nx:nx-ny;return dir[i]?y.localeCompare(x):x.localeCompare(y)}});rows.forEach(r=>tb.appendChild(r))}}
</script></body></html>"""

def publish():
    """Re-publish dashboard.html to Netlify (needs a free account token). Set in /workspace/dashboard/.env:
       NETLIFY_AUTH_TOKEN=...   (Netlify > User settings > Applications > Personal access token)
       NETLIFY_SITE_NAME=esl-dash-<random>   (first run creates the site with this unguessable name)
    Skipped silently if no token. Never prints the token."""
    envp = os.path.join(OUT, ".env")
    if os.path.exists(envp):
        for line in open(envp):
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1); os.environ.setdefault(k, v.strip('"'))
    tok = os.environ.get("NETLIFY_AUTH_TOKEN")
    if not tok: print("publish skipped: no NETLIFY_AUTH_TOKEN"); return
    import io, zipfile, secrets
    h = {"Authorization": "Bearer " + tok, "User-Agent": "dashboard/1.0"}
    sid = os.environ.get("NETLIFY_SITE_ID")
    if not sid:
        name = os.environ.get("NETLIFY_SITE_NAME") or "client-dash-" + secrets.token_hex(8)
        req = urllib.request.Request("https://api.netlify.com/api/v1/sites", data=json.dumps({"name": name}).encode(),
                                     headers={**h, "Content-Type": "application/json"}, method="POST")
        site = json.load(urllib.request.urlopen(req, timeout=60)); sid = site["id"]
        # team default requires Netlify login to view; make this site viewable by anyone with the link
        urllib.request.urlopen(urllib.request.Request(f"https://api.netlify.com/api/v1/sites/{sid}", data=json.dumps({"sso_login": False}).encode(),
                               headers={**h, "Content-Type": "application/json"}, method="PATCH"), timeout=60)
        with open(envp, "a") as f: f.write(f"\nNETLIFY_SITE_ID={sid}\nNETLIFY_SITE_NAME={site['name']}\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.write(os.path.join(OUT, "dashboard.html"), "index.html")
        z.writestr("robots.txt", "User-agent: *\nDisallow: /\n")
        z.writestr("_headers", "/*\n  X-Robots-Tag: noindex, nofollow\n  Cache-Control: no-cache\n")
    req = urllib.request.Request(f"https://api.netlify.com/api/v1/sites/{sid}/deploys", data=buf.getvalue(),
                                 headers={**h, "Content-Type": "application/zip"}, method="POST")
    d = json.load(urllib.request.urlopen(req, timeout=120))
    print("published:", d.get("ssl_url") or d.get("url"))

def load_prev():
    for p in (LAST_GOOD, os.path.join(OUT, "data.json")):
        if os.path.exists(p):
            try: return json.load(open(p))
            except Exception: pass
    return []

def main():
    clients, errors, ok_platforms = [], [], set()
    prev = load_prev()
    for fn in (instantly, sendkit):
        try:
            got = fn(); clients += got; ok_platforms.add(fn.__name__)
        except Exception as e:
            errors.append(f"{fn.__name__} failed: {e}")
            # Keep last-known clients for this platform so a partial outage does not wipe the board
            plat = "Instantly" if fn.__name__ == "instantly" else "Sendkit"
            kept = [c for c in prev if c.get("platform") == plat]
            if kept:
                clients += kept
                errors.append(f"{fn.__name__}: showing last saved {plat} data ({len(kept)} client(s))")
    open(os.path.join(OUT, "dashboard.html"), "w").write(render(clients, errors))
    json.dump(clients, open(os.path.join(OUT, "data.json"), "w"), indent=1)
    # Snapshot only when every platform succeeded, so last_good stays trustworthy
    if not any(x.startswith("instantly failed") or x.startswith("sendkit failed") for x in errors):
        json.dump(clients, open(LAST_GOOD, "w"), indent=1)
    # Append a daily snapshot for history (fallback if an API ever lacks daily pos)
    hist = {"date": TODAY.isoformat(), "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "clients": {x["client"]: {"positive": sum(c["positive"] for c in x["campaigns"]),
                                      "daily_pos": {d: x.get("daily_pos", {}).get(d, 0) for d in DATES}} for x in clients}}
    open(os.path.join(OUT, "history.jsonl"), "a").write(json.dumps(hist) + "\n")
    if "--png" in sys.argv:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            pg.goto("file://" + os.path.join(OUT, "dashboard.html")); pg.wait_for_timeout(1500); pg.screenshot(path=os.path.join(OUT, "dashboard.png"), full_page=True); b.close()
    if "--publish" in sys.argv: publish()
    for x in clients:
        t = totals([x]); print(x["client"], x["platform"], [f(t) for _, f in METRICS])
    T = totals(clients); print("ALL", [f(T) for _, f in METRICS], "errors:", errors)
main()
