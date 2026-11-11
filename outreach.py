"""
OUTREACH DASHBOARD - Google Sheet leads -> Gmail, auto-sending, one dashboard.

Run:  python app.py      then open http://localhost:5000
Click START once. It sends by itself until the daily cap, then waits for tomorrow.
"""
import csv, io, os, random, smtplib, imaplib, sqlite3, threading, time, urllib.request
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import make_msgid
from flask import Flask, jsonify, render_template_string

# ================= SETTINGS =================
GMAIL_ADDRESS  = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS", "")
SHEET_CSV_URL  = os.environ.get("SHEET_CSV_URL", "")   # Sheet > Share > Publish to web > CSV

YOUR_NAME    = "Your Name"
YOUR_ADDRESS = "Your business name, street, city, country"   # required by CAN-SPAM
DAILY_CAP    = 50
GAP_SECONDS  = 120          # 2 minutes
GAP_JITTER   = 20           # +/- seconds so it isn't robotic
ROTATE_EVERY = 5            # switch template every 5 emails
SEND_FROM_HOUR, SEND_TO_HOUR = 8, 18    # only send between these local hours
SHEET_EVERY_MIN, REPLY_EVERY_MIN = 10, 15

OPT_OUT = f'Not interested? Reply "no" and I won\'t email again.\n{YOUR_ADDRESS}'

# Placeholders: {name} {company} {line} {me} {opt}
TEMPLATES = [
    ("Missed calls at {company}",
     "Hi {name},\n\n{line}\n\nWhen someone's AC quits in summer, they call two or three companies and book the first one that answers. If your crew is on a roof, that call goes to voicemail.\n\nI build a missed-call text-back for HVAC businesses: the caller instantly gets a text like \"Sorry we missed you, what's going on with your system? We'll call you right back.\" It plugs into the phone and CRM you already use.\n\nWant a quick mockup of that text for {company}?\n\n{me}\n{opt}"),
    ("Cold night, no heat, {company}",
     "Hi {name},\n\n{line}\n\nWhen a furnace quits on a cold night, people don't leave voicemails. They call the next company until someone picks up.\n\nI build a simple fix: every missed call triggers an instant text to the caller, so the lead stays with {company}. Nothing changes for you or your techs.\n\nWould it help to see that text with {company}'s name on it?\n\n{me}\n{opt}"),
    ("One missed call at {company}",
     "Hi {name},\n\n{line}\n\nOne furnace or AC replacement can be worth thousands. One unanswered call can send that job to a competitor in a minute.\n\nI build an automatic text-back for HVAC businesses. A missed call gets a reply within seconds and it connects to your existing phone setup.\n\nWant me to send a quick example for {company}?\n\n{me}\n{opt}"),
]
# ============================================

app = Flask(__name__)
DB = "outreach.db"
state = {"running": False, "note": "Paused", "last_sheet": 0, "last_reply": 0}
lock = threading.Lock()


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS leads(
            email TEXT PRIMARY KEY, name TEXT, company TEXT, line TEXT,
            status TEXT DEFAULT 'queued', template INTEGER, sent_at TEXT, error TEXT)""")


def pull_sheet():
    if not SHEET_CSV_URL:
        return 0
    raw = urllib.request.urlopen(SHEET_CSV_URL, timeout=30).read().decode("utf-8")
    added = 0
    with db() as c:
        for row in csv.DictReader(io.StringIO(raw)):
            r = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            email = r.get("email", "").lower()
            if "@" not in email:
                continue
            cur = c.execute("INSERT OR IGNORE INTO leads(email,name,company,line) VALUES(?,?,?,?)",
                            (email, r.get("name", ""), r.get("company", ""), r.get("line", "")))
            added += cur.rowcount
    state["last_sheet"] = time.time()
    return added


def check_replies():
    """Mark anyone who replied so they are never emailed again."""
    if not GMAIL_APP_PASS:
        return
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(GMAIL_ADDRESS, GMAIL_APP_PASS)
        imap.select("INBOX", readonly=True)
        with db() as c:
            for r in c.execute("SELECT email FROM leads WHERE status='sent'").fetchall():
                st, data = imap.search(None, "FROM", f'"{r["email"]}"')
                if st == "OK" and data[0].split():
                    c.execute("UPDATE leads SET status='replied' WHERE email=?", (r["email"],))
        imap.logout()
    except Exception as e:
        state["note"] = f"Reply check failed: {e}"
    state["last_reply"] = time.time()


def sent_today():
    today = datetime.now().strftime("%Y-%m-%d")
    with db() as c:
        return c.execute("SELECT COUNT(*) FROM leads WHERE sent_at LIKE ?", (today + "%",)).fetchone()[0]


def total_sent():
    with db() as c:
        return c.execute("SELECT COUNT(*) FROM leads WHERE sent_at IS NOT NULL").fetchone()[0]


def send_one(lead, tpl):
    subject_t, body_t = TEMPLATES[tpl]
    v = dict(name=lead["name"] or "there", company=lead["company"] or "your business",
             line=lead["line"], me=YOUR_NAME, opt=OPT_OUT)
    body = body_t.format(**v)
    while "\n\n\n" in body:
        body = body.replace("\n\n\n", "\n\n")
    msg = EmailMessage()
    msg["From"] = f"{YOUR_NAME} <{GMAIL_ADDRESS}>"
    msg["To"] = lead["email"]
    msg["Subject"] = subject_t.format(**v)
    msg["Message-ID"] = make_msgid()
    msg["List-Unsubscribe"] = f"<mailto:{GMAIL_ADDRESS}?subject=unsubscribe>"
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_ADDRESS, GMAIL_APP_PASS)
        s.send_message(msg)


def nap(seconds):
    for _ in range(int(seconds)):
        if not state["running"]:
            return
        time.sleep(1)


def worker():
    while True:
        try:
            if not state["running"]:
                time.sleep(2)
                continue
            if time.time() - state["last_sheet"] > SHEET_EVERY_MIN * 60:
                pull_sheet()
            if time.time() - state["last_reply"] > REPLY_EVERY_MIN * 60:
                check_replies()
            if not (GMAIL_ADDRESS and GMAIL_APP_PASS):
                state["note"] = "Set GMAIL_ADDRESS and GMAIL_APP_PASS"
                nap(5)
                continue
            if not (SEND_FROM_HOUR <= datetime.now().hour < SEND_TO_HOUR):
                state["note"] = f"Outside sending hours ({SEND_FROM_HOUR}:00-{SEND_TO_HOUR}:00)"
                nap(30)
                continue
            if sent_today() >= DAILY_CAP:
                state["note"] = "Daily cap reached. Resumes tomorrow."
                nap(60)
                continue
            with db() as c:
                lead = c.execute("SELECT * FROM leads WHERE status='queued' LIMIT 1").fetchone()
            if not lead:
                state["note"] = "No new leads. Add rows to your sheet."
                nap(30)
                continue
            tpl = (total_sent() // ROTATE_EVERY) % len(TEMPLATES)
            try:
                send_one(lead, tpl)
                with db() as c:
                    c.execute("UPDATE leads SET status='sent', template=?, sent_at=? WHERE email=?",
                              (tpl, datetime.now().strftime("%Y-%m-%d %H:%M"), lead["email"]))
                state["note"] = f"Sent to {lead['email']}"
            except Exception as e:
                with db() as c:
                    c.execute("UPDATE leads SET status='error', error=? WHERE email=?", (str(e)[:200], lead["email"]))
                state["note"] = f"Error: {e}"
            nap(GAP_SECONDS + random.uniform(-GAP_JITTER, GAP_JITTER))
        except Exception as e:
            state["note"] = f"Worker error: {e}"
            time.sleep(10)


@app.route("/api/stats")
def stats():
    with db() as c:
        counts = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM leads GROUP BY status")}
        days = []
        for i in range(6, -1, -1):
            d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            n = c.execute("SELECT COUNT(*) FROM leads WHERE sent_at LIKE ?", (d + "%",)).fetchone()[0]
            days.append({"day": d[5:], "n": n})
        recent = [dict(r) for r in c.execute(
            "SELECT email, company, template, sent_at, status FROM leads WHERE sent_at IS NOT NULL ORDER BY sent_at DESC LIMIT 12")]
    return jsonify(counts=counts, days=days, recent=recent, today=sent_today(), cap=DAILY_CAP,
                   running=state["running"], note=state["note"])


@app.route("/api/start", methods=["POST"])
def start():
    state["running"] = True
    state["note"] = "Running"
    return jsonify(ok=True)


@app.route("/api/pause", methods=["POST"])
def pause():
    state["running"] = False
    state["note"] = "Paused"
    return jsonify(ok=True)


@app.route("/api/pull", methods=["POST"])
def pull():
    try:
        return jsonify(added=pull_sheet())
    except Exception as e:
        return jsonify(error=str(e)), 500


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Outreach control</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#f3f5f2;--card:#fff;--ink:#16211b;--mute:#6a766f;--line:#dfe5e0;--green:#1f7a4d;--amber:#d99a1c;--blue:#2f6fb3;--red:#c4452f}
@media(prefers-color-scheme:dark){:root{--bg:#121714;--card:#1b231e;--ink:#e8efe9;--mute:#93a198;--line:#2a352e}}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--ink);padding:16px;max-width:980px;margin:auto}
h1{font-size:22px;margin:4px 0 2px}.sub{color:var(--mute);margin-bottom:14px}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin-bottom:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.card h2{font-size:14px;margin:0 0 10px;color:var(--mute);font-weight:600}
.big{font-size:34px;font-weight:700}.bar{height:10px;background:var(--line);border-radius:6px;overflow:hidden;margin-top:8px}
.bar i{display:block;height:100%;background:var(--green);width:0}
button{font:inherit;padding:9px 16px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}
button.go{background:var(--green);color:#fff;border-color:var(--green)}button:focus-visible{outline:2px solid var(--blue)}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{text-align:left;padding:6px 4px;border-bottom:1px solid var(--line)}
.wrap{overflow-x:auto}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;background:var(--mute)}
canvas{max-height:260px}
</style></head><body>
<h1>Outreach control</h1><div class="sub" id="note">Loading...</div>
<div class="row">
 <div class="card"><h2>Sent today</h2><div class="big"><span id="today">0</span> / <span id="cap">50</span></div>
  <div class="bar"><i id="fill"></i></div>
  <div style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap">
   <button class="go" onclick="post('start')">Start sending</button>
   <button onclick="post('pause')">Pause</button>
   <button onclick="pullNow()">Pull sheet now</button></div></div>
 <div class="card"><h2>All leads by status</h2><canvas id="pie"></canvas></div>
</div>
<div class="row">
 <div class="card"><h2>Emails sent, last 7 days</h2><canvas id="bars"></canvas></div>
 <div class="card"><h2>Latest sends</h2><div class="wrap"><table><thead><tr><th>Company</th><th>Template</th><th>Time</th><th>Status</th></tr></thead><tbody id="recent"></tbody></table></div></div>
</div>
<script>
const col={queued:'#2f6fb3',sent:'#1f7a4d',replied:'#d99a1c',error:'#c4452f'};
let pie,bars;
async function post(p){await fetch('/api/'+p,{method:'POST'});load()}
async function pullNow(){const r=await fetch('/api/pull',{method:'POST'});const j=await r.json();alert(j.error?j.error:j.added+' new leads added');load()}
async function load(){
 const s=await (await fetch('/api/stats')).json();
 document.getElementById('note').textContent=(s.running?'Running - ':'Paused - ')+s.note;
 document.getElementById('today').textContent=s.today;document.getElementById('cap').textContent=s.cap;
 document.getElementById('fill').style.width=Math.min(100,s.today/s.cap*100)+'%';
 const k=Object.keys(s.counts);
 const pd={labels:k,datasets:[{data:k.map(x=>s.counts[x]),backgroundColor:k.map(x=>col[x]||'#888')}]};
 if(!pie)pie=new Chart(pieC=document.getElementById('pie'),{type:'doughnut',data:pd,options:{animation:false}});else{pie.data=pd;pie.update()}
 const bd={labels:s.days.map(d=>d.day),datasets:[{data:s.days.map(d=>d.n),backgroundColor:'#1f7a4d'}]};
 if(!bars)bars=new Chart(document.getElementById('bars'),{type:'bar',data:bd,options:{animation:false,plugins:{legend:{display:false}}}});else{bars.data=bd;bars.update()}
 document.getElementById('recent').innerHTML=s.recent.map(r=>`<tr><td>${r.company||r.email}</td><td>${r.template+1}</td><td>${r.sent_at.slice(11)}</td><td><span class="dot" style="background:${col[r.status]}"></span>${r.status}</td></tr>`).join('')||'<tr><td colspan=4>Nothing sent yet</td></tr>';
}
load();setInterval(load,5000);
</script></body></html>"""


@app.route("/")
def home():
    return render_template_string(PAGE)


if __name__ == "__main__":
    init()
    threading.Thread(target=worker, daemon=True).start()
    app.run(host="127.0.0.1", port=5000)
