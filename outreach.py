"""
OUTREACH SYSTEM (pure Python, no Google Sheets)

SETUP
1. In your Google account turn on 2-Step Verification, then create an
   "App Password" (Google Account > Security > App passwords).
2. Set environment variables (or type them below):
     GMAIL_ADDRESS   = yourname@gmail.com   (check spelling: gmail, not gmial)
     GMAIL_APP_PASS  = the 16-character app password
3. Make leads.csv in the same folder with this header row:
     name,company,email,line,status,sent_time
   Leave status and sent_time empty for new leads.
   "line" = one personal sentence per business.

USAGE
   python outreach.py drafts            -> creates Gmail drafts, marks rows "Drafted"
   python outreach.py send              -> sends drafted rows slowly
   python outreach.py send --at 16:00   -> waits until 4 PM, then sends
"""
import csv, os, sys, time, random, smtplib, imaplib
from datetime import datetime
from email.message import EmailMessage

# ===== SETTINGS =====
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "yourname@gmail.com")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS", "")
YOUR_NAME = "Your Name"
CSV_FILE = "leads.csv"
DAILY_CAP = 15
MIN_GAP_MIN, MAX_GAP_MIN = 2, 4
ROTATE_EVERY = 5

TEMPLATES = [
    ("Missed calls at {company}",
     """Hi {name},

{line}

When you're on a job or with a customer, calls go unanswered, and most people don't leave a voicemail. They call the next business on Google.

I build a missed-call text-back system: the moment you miss a call, the caller automatically gets a text like "Sorry we missed you, how can we help?" so the lead stays with you. It's custom-built to plug into the phone and CRM setup you already have.

I'm taking on a few clients now, with a small deposit upfront so I build it around your business.

Do you ever miss calls when you're on a job? If so, want me to send a quick example for {company}?

{me}
Not interested? Reply "no" and I won't email again."""),
    ("Quick question, {company}",
     """Hi {name},

{line}

Quick question: when you're busy and the phone rings, what happens to that caller? Most move on to the next option within minutes.

I set up a simple fix: a missed call triggers an instant text back, so the lead stays with you until you're free. It plugs into your existing phone setup.

Would a short example written for {company} be useful?

{me}
Not interested? Reply "no" and I won't email again."""),
    ("Leads slipping through at {company}?",
     """Hi {name},

{line}

One missed call can be a big job for a business like {company}. The problem is callers rarely wait or leave voicemails.

I build an automatic text-back that replies to every missed call within seconds. It's custom-built and connects to what you already use, so nothing changes on your end.

Open to seeing how it would look for {company}?

{me}
Not interested? Reply "no" and I won't email again."""),
]

FIELDS = ["name", "company", "email", "line", "status", "sent_time"]


def load():
    with open(CSV_FILE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save(rows):
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def build(row, index):
    subject_t, body_t = TEMPLATES[(index // ROTATE_EVERY) % len(TEMPLATES)]
    vals = dict(
        name=row["name"] or "there",
        company=row["company"] or "your business",
        line=row["line"] or "",
        me=YOUR_NAME,
    )
    msg = EmailMessage()
    msg["From"] = f"{YOUR_NAME} <{GMAIL_ADDRESS}>"
    msg["To"] = row["email"]
    msg["Subject"] = subject_t.format(**vals)
    msg.set_content(body_t.format(**vals).replace("\n\n\n", "\n\n"))
    return msg


def make_drafts():
    rows = load()
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(GMAIL_ADDRESS, GMAIL_APP_PASS)
    made = 0
    for i, r in enumerate(rows):
        if r["status"] or not r["email"]:
            continue
        msg = build(r, i)
        imap.append("[Gmail]/Drafts", "\\Draft",
                    imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        r["status"] = "Drafted"
        made += 1
    imap.logout()
    save(rows)
    print(f"{made} drafts created. Review them in Gmail.")


def send_all(start_at=None):
    if start_at:
        h, m = map(int, start_at.split(":"))
        while True:
            n = datetime.now()
            if (n.hour, n.minute) >= (h, m):
                break
            print(f"Waiting until {start_at}... (now {n:%H:%M})", end="\r")
            time.sleep(20)

    rows = load()
    today = datetime.now().strftime("%Y-%m-%d")
    sent_today = sum(1 for r in rows if r["sent_time"].startswith(today))

    for i, r in enumerate(rows):
        if r["status"] != "Drafted":
            continue
        if sent_today >= DAILY_CAP:
            print(f"Daily cap of {DAILY_CAP} reached. Stopping.")
            break
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
                s.login(GMAIL_ADDRESS, GMAIL_APP_PASS)
                s.send_message(build(r, i))
            r["status"] = "Sent"
            r["sent_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            sent_today += 1
            print(f"Sent to {r['email']} ({sent_today}/{DAILY_CAP})")
        except Exception as e:
            r["status"] = f"Error: {e}"
            print(f"Failed for {r['email']}: {e}")
        save(rows)
        if sent_today < DAILY_CAP:
            time.sleep(random.uniform(MIN_GAP_MIN, MAX_GAP_MIN) * 60)
    save(rows)
    print("Done.")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("drafts", "send"):
        print(__doc__)
    elif sys.argv[1] == "drafts":
        make_drafts()
    else:
        at = sys.argv[sys.argv.index("--at") + 1] if "--at" in sys.argv else None
        send_all(at)
