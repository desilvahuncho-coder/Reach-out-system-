"""
OUTREACH SYSTEM v2 (HVAC) - Python + Gmail + Google Sheet leads

SETUP
1. Google Account: turn on 2-Step Verification, then create an App Password.
2. Set environment variables (never put them in GitHub):
     GMAIL_ADDRESS   = yourname@gmail.com   (gmail, not gmial)
     GMAIL_APP_PASS  = 16-character app password
     SHEET_CSV_URL   = (optional) your Google Sheet "leads" published as CSV
3. Google Sheet named "leads" with these headers in row 1:
     name | company | email | line
   line = one small personal fact, e.g. "Saw you've got 180 reviews and offer 24/7 service."
   To get SHEET_CSV_URL: File > Share > Publish to web > choose the leads sheet
   > format "CSV" > Publish > copy the link.
   (Anyone with the link can read it, so keep sensitive data out of that sheet.)
   No link? Download the sheet as CSV and name it leads.csv instead.

COMMANDS
   python outreach.py pull                -> adds NEW leads from the sheet to leads.csv
   python outreach.py drafts              -> makes Gmail drafts for new leads (review them)
   python outreach.py send [--at 16:00]   -> sends drafted first emails slowly
   python outreach.py followups [--at 16:00] -> sends due follow-ups (skips anyone who replied)
"""
import csv, os, sys, time, random, smtplib, imaplib, urllib.request, io
from datetime import datetime
from email.message import EmailMessage
from email.utils import make_msgid

# ===== SETTINGS =====
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "yourname@gmail.com")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS", "")
SHEET_CSV_URL = os.environ.get("SHEET_CSV_URL", "")
YOUR_NAME = "Your Name"
CSV_FILE = "leads.csv"
DAILY_CAP = 15                 # first emails + follow-ups combined
MIN_GAP_MIN, MAX_GAP_MIN = 2, 4
ROTATE_EVERY = 5               # switch first-email template every 5 leads
FOLLOWUP_AFTER_DAYS = [3, 4, 7]  # days since last contact (follow-ups land ~day 3, 7, 14)

OPT_OUT = 'Not interested? Reply "no" and I won\'t email again.'

# ===== FIRST EMAILS (3) =====
# Placeholders: {name} {company} {line} {me}
TEMPLATES = [
    ("Missed calls at {company}",
     """Hi {name},

{line}

When someone's AC quits in the middle of summer, they call two or three HVAC companies and book the first one that answers. If your crew is on a roof or in an attic, that call goes to voicemail and the job goes to the next company.

I build a missed-call text-back for HVAC businesses. The moment a call goes unanswered, the caller gets a text like "Sorry we missed you, what's going on with your system? We'll call you right back." The lead stays yours until you're free. It's custom-built to plug into the phone and CRM you already use.

I'm taking on a few HVAC clients before the holidays, with a small deposit so I build it around how {company} works.

How many calls do you think you miss in a week when the crews are out? If it's more than a couple, want me to send a quick mockup of the text for {company}?

{me}
{opt}"""),
    ("Cold night, no heat, {company}",
     """Hi {name},

{line}

When a furnace quits on a cold night, people don't leave voicemails. They call the next heating company on the list until someone picks up.

I build a simple fix for HVAC shops: every missed call triggers an instant text to the caller, so they know you're on it and the lead stays with {company}. It's custom-built and connects to the phone setup you already have, so nothing changes for you or your techs.

I work with a small deposit upfront so the system is built around your business, not a generic template.

Would it help to see what that text would look like with {company}'s name on it?

{me}
{opt}"""),
    ("One missed call at {company}",
     """Hi {name},

{line}

One furnace or AC replacement can be worth several thousand dollars. One unanswered call can send that job to a competitor in about a minute.

I build an automatic text-back for HVAC businesses. A missed call gets a reply within seconds, which keeps the customer waiting for you instead of dialing the next company. It plugs into your existing phone and CRM, and it's built around how {company} takes jobs.

I'm taking on a few HVAC clients now, with a small deposit to start.

If your phone is ever unanswered during a job, is that something you'd want to fix before the busy stretch? I can send a quick example for {company}.

{me}
{opt}"""),
]

# ===== FOLLOW-UPS (3) =====
FOLLOWUPS = [
    # Follow-up 1 (about day 3): short bump
    """Hi {name},

Following up on my note about missed calls at {company}. When the crews are out, even one unanswered call a week adds up to real lost jobs.

Do you want me to send over a quick example of the text-back for {company}? It takes me a few minutes and there's no obligation.

{me}
{opt}""",
    # Follow-up 2 (about day 7): show the product
    """Hi {name},

Here's what your customer would get the moment a call goes unanswered:

"Hi, this is {company}. Sorry we missed your call. What's going on with your system? We'll call you back shortly."

That's the whole idea. The caller feels looked after, and the lead stays with you while you finish the job. I'd build it to match your voice and connect to your current phone setup.

Want me to set it up for {company}? I start with a small deposit and build around how you work.

{me}
{opt}"""
    ,
    # Follow-up 3 (about day 14): polite close
    """Hi {name},

I'll close the loop here so I don't clutter your inbox. If missed calls ever become a problem at {company}, reply to this and I'll put together a quick example for you.

Wishing you a strong season.

{me}
{opt}""",
]

FIELDS = ["name", "company", "email", "line", "status", "sent_time",
          "last_contact", "follow_ups", "msgid", "tpl"]


# ===== FILE HELPERS =====
def load():
    if not os.path.exists(CSV_FILE):
        return []
    with open(CSV_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in FIELDS:
            r[k] = (r.get(k) or "").strip()
    return rows


def save(rows):
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def days_since(ts):
    return (datetime.now() - datetime.strptime(ts, "%Y-%m-%d %H:%M")).days


# ===== PULL LEADS FROM GOOGLE SHEET =====
def pull():
    if not SHEET_CSV_URL:
        print("SHEET_CSV_URL not set. Download your sheet as leads.csv instead.")
        return
    raw = urllib.request.urlopen(SHEET_CSV_URL).read().decode("utf-8")
    incoming = list(csv.DictReader(io.StringIO(raw)))
    rows = load()
    have = {r["email"].lower() for r in rows}
    added = 0
    for n in incoming:
        n = {k.strip().lower(): (v or "").strip() for k, v in n.items() if k}
        email = n.get("email", "")
        if not email or email.lower() in have:
            continue
        new = {k: "" for k in FIELDS}
        new.update(name=n.get("name", ""), company=n.get("company", ""),
                   email=email, line=n.get("line", ""))
        rows.append(new)
        have.add(email.lower())
        added += 1
    save(rows)
    print(f"{added} new leads added. Total: {len(rows)}.")


# ===== BUILD EMAILS =====
def vals(r):
    return dict(name=r["name"] or "there",
                company=r["company"] or "your business",
                line=r["line"], me=YOUR_NAME, opt=OPT_OUT)


def build_first(r, index):
    t = (index // ROTATE_EVERY) % len(TEMPLATES)
    subject_t, body_t = TEMPLATES[t]
    msg = EmailMessage()
    msg["From"] = f"{YOUR_NAME} <{GMAIL_ADDRESS}>"
    msg["To"] = r["email"]
    msg["Subject"] = subject_t.format(**vals(r))
    body = body_t.format(**vals(r))
    while "\n\n\n" in body:
        body = body.replace("\n\n\n", "\n\n")
    msg.set_content(body)
    return msg, t


def build_followup(r):
    n = int(r["follow_ups"] or 0)
    subject = "Re: " + TEMPLATES[int(r["tpl"] or 0)][0].format(**vals(r))
    msg = EmailMessage()
    msg["From"] = f"{YOUR_NAME} <{GMAIL_ADDRESS}>"
    msg["To"] = r["email"]
    msg["Subject"] = subject
    if r["msgid"]:
        msg["In-Reply-To"] = r["msgid"]
        msg["References"] = r["msgid"]
    msg.set_content(FOLLOWUPS[n].format(**vals(r)))
    return msg


# ===== DRAFTS =====
def make_drafts():
    rows = load()
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(GMAIL_ADDRESS, GMAIL_APP_PASS)
    made = 0
    for i, r in enumerate(rows):
        if r["status"] or not r["email"]:
            continue
        msg, t = build_first(r, i)
        imap.append("[Gmail]/Drafts", "\\Draft",
                    imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        r["status"] = "Drafted"
        r["tpl"] = str(t)
        made += 1
    imap.logout()
    save(rows)
    print(f"{made} drafts created. Review them in Gmail, then run: send")


# ===== REPLY CHECK =====
def has_replied(imap, email):
    imap.select("INBOX", readonly=True)
    status, data = imap.search(None, "FROM", f'"{email}"')
    return status == "OK" and bool(data[0].split())


# ===== SENDING =====
def wait_until(start_at):
    if not start_at:
        return
    h, m = map(int, start_at.split(":"))
    while (datetime.now().hour, datetime.now().minute) < (h, m):
        print(f"Waiting until {start_at}... (now {datetime.now():%H:%M})", end="\r")
        time.sleep(20)


def sent_today_count(rows):
    today = datetime.now().strftime("%Y-%m-%d")
    return sum(1 for r in rows if r["last_contact"].startswith(today))


def smtp_send(msg):
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_ADDRESS, GMAIL_APP_PASS)
        s.send_message(msg)


def send_first(start_at=None):
    wait_until(start_at)
    rows = load()
    count = sent_today_count(rows)
    for i, r in enumerate(rows):
        if r["status"] != "Drafted":
            continue
        if count >= DAILY_CAP:
            print(f"Daily cap of {DAILY_CAP} reached. Stopping.")
            break
        try:
            msg, t = build_first(r, i)
            msg["Message-ID"] = make_msgid()
            smtp_send(msg)
            r.update(status="Sent", sent_time=now_str(), last_contact=now_str(),
                     follow_ups="0", msgid=msg["Message-ID"], tpl=str(t))
            count += 1
            print(f"Sent to {r['email']} ({count}/{DAILY_CAP})")
        except Exception as e:
            r["status"] = f"Error: {e}"
            print(f"Failed for {r['email']}: {e}")
        save(rows)
        if count < DAILY_CAP:
            time.sleep(random.uniform(MIN_GAP_MIN, MAX_GAP_MIN) * 60)
    save(rows)
    print("Done.")


def send_followups(start_at=None):
    wait_until(start_at)
    rows = load()
    count = sent_today_count(rows)
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(GMAIL_ADDRESS, GMAIL_APP_PASS)

    for r in rows:
        if r["status"] != "Sent":
            continue
        n = int(r["follow_ups"] or 0)
        if n >= len(FOLLOWUPS):
            r["status"] = "Done"
            continue
        if days_since(r["last_contact"]) < FOLLOWUP_AFTER_DAYS[n]:
            continue
        # stop if they answered (including "no")
        if has_replied(imap, r["email"]):
            r["status"] = "Replied"
            print(f"{r['email']} replied. Check your inbox. Skipping follow-ups.")
            save(rows)
            continue
        if count >= DAILY_CAP:
            print(f"Daily cap of {DAILY_CAP} reached. Stopping.")
            break
        try:
            smtp_send(build_followup(r))
            r["follow_ups"] = str(n + 1)
            r["last_contact"] = now_str()
            count += 1
            print(f"Follow-up {n + 1} sent to {r['email']} ({count}/{DAILY_CAP})")
        except Exception as e:
            print(f"Failed for {r['email']}: {e}")
        save(rows)
        if count < DAILY_CAP:
            time.sleep(random.uniform(MIN_GAP_MIN, MAX_GAP_MIN) * 60)
    imap.logout()
    save(rows)
    print("Done.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    at = sys.argv[sys.argv.index("--at") + 1] if "--at" in sys.argv else None
    if cmd == "pull":
        pull()
    elif cmd == "drafts":
        make_drafts()
    elif cmd == "send":
        send_first(at)
    elif cmd == "followups":
        send_followups(at)
    else:
        print(__doc__)
