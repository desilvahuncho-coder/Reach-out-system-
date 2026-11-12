
const CONFIG = {
  SPREADSHEET_ID: "14nLJefB88kUMrd3Cs6PLLfPG16DLF47iqB7ThtrUoAo",
  SHEET_NAME: "Sheet1",
  DAILY_LIMIT: 50,
  SENDER_NAME: "Your Name"
};

const EMAIL_TEMPLATES = [
  {
    name: "Template A",
    subject: "Missed calls at {company}",
    body: `Hi {name},

{line}

When the trucks are out and the phone rings, half the people who hit voicemail just hang up and call the next company. That can be a $400 service call or an $8–12k system walking out the door.

I built something that sits inside ServiceTitan / HCP / Jobber (not on top of it). Missed or after-hours call → natural text in under 5 seconds → it asks if it's urgent or routine, then either books the right open slot on the board or warm-transfers the emergency to your on-call tech. Notes and recording land on the job card automatically. No duplicate customers, no messy dispatch.

Small deposit to map your zones and fees. Live in a few days. One recovered call usually covers the whole month.

Want me to send a 20-second mock of the exact text {company} customers would get?

{your name}

Not interested? Reply "no" and I'll stop.`
  },
  {
    name: "Template B",
    subject: "Cold night, no heat — {company}",
    body: `Hi {name},

{line}

Furnace dies at 9 p.m., customer hits voicemail, and they're already dialing the next number. Answering services and the built-in tools in ServiceTitan/HCP catch some of those, but a lot still slip.

What I do is different: the second the call is missed, a real-sounding text goes out, triages whether it's an emergency or a normal repair, then either drops a booked job straight onto the right tech's schedule or gets your on-call guy on the phone. Everything stays clean in the CRM you already live in.

No 12-month lock-in. We start with a small deposit, map your service areas and capacity, and have it live fast. If it doesn't pay for itself with one or two recovered calls, you walk away.

Happy to show you exactly what the customer conversation looks like for {company}.

{your name}

Reply "no" if you want me to leave you alone.`
  },
  {
    name: "Template C",
    subject: "One missed call at {company}",
    body: `Hi {name},

{line}

One unanswered call during peak season can be a multi-thousand-dollar job gone. Most of the time the person doesn't even leave a message — they just call the next company.

I plug directly into ServiceTitan, HCP, or Jobber so the system can see real tech availability, book the job in the correct zone, tag the source properly, and push notes/recording to the customer's job card. Emergencies get warm-transferred. Routine work gets booked without your office touching it.

Setup is about 30 minutes with whoever runs your admin. No long contracts. One recovered service call usually covers the cost.

Want a quick example of the text + booking flow with {company}'s name on it?

{your name}

Not interested? Just say "no."`
  }
];

const FOLLOWUP_TEMPLATES = [
  {
    name: "Follow-up 1",
    day: 3,
    subject: "Re: Missed calls at {company}",
    body: `Hi {name},

Quick follow-up on the missed-call note.

The biggest fear I hear from owners is "will this mess up my dispatch board or create duplicate customers?" That's exactly why it lives inside ServiceTitan/HCP/Jobber instead of sitting on top of it.

Still want me to send the short mock so you can see how clean it stays?

{your name}

Reply "no" and I'll close the file.`
  },
  {
    name: "Follow-up 2",
    day: 7,
    subject: "Re: Missed calls at {company}",
    body: `Hi {name},

Here's the actual first text the customer gets:

"Hey, this is {company} — sorry we just missed your call. Is this an urgent heating/cooling issue or are you looking to schedule a visit?"

From there it either books the open slot on the board or gets the right person on the phone. Everything lands cleanly in the CRM you already use.

Small deposit, live in a few days, no long lock-in. One recovered call pays for the month.

Want me to set it up for {company}?

{your name}

Not interested → just reply "no."`
  },
  {
    name: "Follow-up 3",
    day: 14,
    subject: "Re: Missed calls at {company}",
    body: `Hi {name},

Last note so I don't keep hitting your inbox.

If missed or after-hours calls are ever costing {company} jobs, reply and I'll put together a 30-second walkthrough of how it sits inside your current software without touching the dispatch board.

Hope the season treats you well.

{your name}`
  }
];

// DASHBOARD ENTRY POINT
function doGet() {
  return HtmlService.createHtmlOutputFromFile("index")
    .setTitle("Outreach HQ")
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.DEFAULT);
}

// INITIALIZE SHEETS AND TRACKING COLUMNS
function getSheet() {
  const ss = SpreadsheetApp.openById(CONFIG.SPREADSHEET_ID);
  const sheet = ss.getSheetByName(CONFIG.SHEET_NAME);

  if (!sheet) throw new Error("Sheet1 not found.");

  const headers = [
    "Template used",
    "Gmail draft ID",
    "Gmail thread ID",
    "Follow-up stage",
    "Initial sent date",
    "Draft created at"
  ];

  headers.forEach((header, i) => {
    const cell = sheet.getRange(1, i + 8);
    if (!cell.getValue()) cell.setValue(header);
  });

  return sheet;
}

function getActivitySheet() {
  const ss = SpreadsheetApp.openById(CONFIG.SPREADSHEET_ID);
  let sheet = ss.getSheetByName("Activity");

  if (!sheet) {
    sheet = ss.insertSheet("Activity");
    sheet.appendRow([
      "Date", "Email", "Company", "Event", "Template"
    ]);
  }

  return sheet;
}

function logActivity(email, company, event, template) {
  getActivitySheet().appendRow([
    new Date(), email, company, event, template
  ]);
}

// TEMPLATE PERSONALIZATION
function personalize(template, lead) {
  const values = {
    name: String(lead.name || "there"),
    company: String(lead.company || "your company"),
    line: String(
      lead.businessInfo ||
      "I came across your business and wanted to reach out."
    ),
    "your name": CONFIG.SENDER_NAME
  };

  function replace(text) {
    return text.replace(
      /\{(your name|name|company|line)\}/gi,
      (match, key) => values[key.toLowerCase()] || match
    );
  }

  return {
    subject: replace(template.subject),
    body: replace(template.body)
  };
}

function readLead(row, rowNumber) {
  return {
    rowNumber,
    name: row[0],
    company: row[1],
    email: String(row[2] || "").trim(),
    businessInfo: row[3],
    status: String(row[4] || "").trim(),
    lastContacted: row[5],
    followUpDate: row[6],
    template: row[7],
    draftId: row[8],
    threadId: row[9],
    followUpStage: Number(row[10] || 0),
    initialSentDate: row[11],
    draftCreatedAt: row[12]
  };
}

// CREATE INITIAL GMAIL DRAFTS
function prepareDailyDrafts() {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);

  try {
    const sheet = getSheet();
    const lastRow = sheet.getLastRow();

    if (lastRow < 2) {
      return { success: false, message: "No leads found." };
    }

    const data = sheet.getRange(2, 1, lastRow - 1, 13).getValues();
    const today = Utilities.formatDate(
      new Date(), Session.getScriptTimeZone(), "yyyy-MM-dd"
    );

    const alreadyPrepared = data.filter(row => {
      const date = row[12];
      return date instanceof Date &&
        Utilities.formatDate(
          date, Session.getScriptTimeZone(), "yyyy-MM-dd"
        ) === today;
    }).length;

    let prepared = 0;
    let skipped = 0;
    let assigned = data.filter(row => row[7]).length;

    for (let i = 0; i < data.length; i++) {
      if (alreadyPrepared + prepared >= CONFIG.DAILY_LIMIT) break;

      const lead = readLead(data[i], i + 2);
      const status = lead.status.toLowerCase();

      if (
        !lead.email ||
        !lead.email.includes("@") ||
        lead.draftId ||
        (status && status !== "new")
      ) {
        skipped++;
        continue;
      }

      const templateIndex =
        Math.floor(assigned / 5) % EMAIL_TEMPLATES.length;

      const template = EMAIL_TEMPLATES[templateIndex];
      const email = personalize(template, lead);

      const draft = GmailApp.createDraft(
        lead.email,
        email.subject,
        email.body
      );

      sheet.getRange(lead.rowNumber, 5).setValue("Draft");
      sheet.getRange(lead.rowNumber, 8).setValue(template.name);
      sheet.getRange(lead.rowNumber, 9).setValue(draft.getId());
      sheet.getRange(lead.rowNumber, 13).setValue(new Date());

      assigned++;
      prepared++;
    }

    return {
      success: true,
      prepared,
      skipped,
      message: prepared + " drafts prepared."
    };

  } finally {
    lock.releaseLock();
  }
}

// CREATE FOLLOW-UP DRAFTS WHEN THEY BECOME DUE
function prepareFollowupDrafts() {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);

  try {
    const sheet = getSheet();
    const data = sheet.getDataRange().getValues();
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    let prepared = 0;

    for (let i = 1; i < data.length; i++) {
      const lead = readLead(data[i], i + 1);

      if (
        lead.status.toLowerCase() !== "sent" ||
        !lead.email ||
        lead.draftId ||
        !(lead.initialSentDate instanceof Date)
      ) continue;

      const stage = lead.followUpStage;

      if (stage >= 3) continue;

      const dueDate = new Date(lead.initialSentDate);
      dueDate.setDate(
        dueDate.getDate() + FOLLOWUP_TEMPLATES[stage].day
      );
      dueDate.setHours(0, 0, 0, 0);

      if (today < dueDate) continue;

      const template = FOLLOWUP_TEMPLATES[stage];
      const email = personalize(template, lead);

      const draft = GmailApp.createDraft(
        lead.email,
        email.subject,
        email.body
      );

      sheet.getRange(lead.rowNumber, 5).setValue("Follow-up Draft");
      sheet.getRange(lead.rowNumber, 8).setValue(template.name);
      sheet.getRange(lead.rowNumber, 9).setValue(draft.getId());
      sheet.getRange(lead.rowNumber, 13).setValue(new Date());

      prepared++;
    }

    return {
      success: true,
      prepared,
      message: prepared + " follow-up drafts prepared."
    };

  } finally {
    lock.releaseLock();
  }
}

// SEND ONLY DRAFTS SELECTED IN THE DASHBOARD
function sendSelectedDrafts(draftIds) {
  if (!Array.isArray(draftIds) || !draftIds.length) {
    throw new Error("Select at least one draft.");
  }

  const lock = LockService.getScriptLock();
  lock.waitLock(30000);

  try {
    const sheet = getSheet();
    const data = sheet.getDataRange().getValues();
    const results = [];

    for (const id of draftIds) {
      let found = false;

      for (let i = 1; i < data.length; i++) {
        const lead = readLead(data[i], i + 1);

        if (String(lead.draftId) !== String(id)) continue;

        found = true;

        if (!["Draft", "Follow-up Draft"].includes(lead.status)) {
          throw new Error("Lead is not awaiting approval.");
        }

        const draft = GmailApp.getDraft(id);
        if (!draft) {
          throw new Error("Draft not found. Refresh your dashboard.");
        }

        const message = draft.send();
        const now = new Date();
        const isFollowup =
          lead.status === "Follow-up Draft";

        const stage = isFollowup
          ? lead.followUpStage + 1
          : 0;

        const initialDate = lead.initialSentDate || now;

        sheet.getRange(lead.rowNumber, 5).setValue("Sent");
        sheet.getRange(lead.rowNumber, 6).setValue(now);
        sheet.getRange(lead.rowNumber, 7).setValue(
          stage < 3
            ? new Date(
                new Date(initialDate).getTime() +
                FOLLOWUP_TEMPLATES[stage].day * 86400000
              )
            : ""
        );
        sheet.getRange(lead.rowNumber, 9).clearContent();
        sheet.getRange(lead.rowNumber, 10)
          .setValue(message.getThread().getId());
        sheet.getRange(lead.rowNumber, 11).setValue(stage);
        sheet.getRange(lead.rowNumber, 12).setValue(initialDate);

        logActivity(
          lead.email, lead.company, "Sent", lead.template
        );

        results.push(lead.email);
        break;
      }

      if (!found) {
        throw new Error("A selected draft does not belong to this campaign.");
      }
    }

    return {
      success: true,
      sent: results.length,
      message: results.length + " emails sent."
    };

  } finally {
    lock.releaseLock();
  }
}

// DETECT REPLIES IN TRACKED GMAIL THREADS
function syncReplies() {
  const sheet = getSheet();
  const data = sheet.getDataRange().getValues();
  const ownEmail = Session.getActiveUser().getEmail().toLowerCase();
  let replies = 0;

  for (let i = 1; i < data.length; i++) {
    const lead = readLead(data[i], i + 1);

    if (
      lead.status.toLowerCase() !== "sent" ||
      !lead.threadId
    ) continue;

    const thread = GmailApp.getThreadById(lead.threadId);
    if (!thread) continue;

    const messages = thread.getMessages();

    const hasReply = messages.some(message => {
      const sender = message.getFrom().toLowerCase();
      return !sender.includes(ownEmail) &&
        sender.includes(lead.email.toLowerCase());
    });

    if (hasReply) {
      sheet.getRange(lead.rowNumber, 5).setValue("Replied");
      sheet.getRange(lead.rowNumber, 7).clearContent();
      logActivity(lead.email, lead.company, "Replied", lead.template);
      replies++;
    }
  }

  return { success: true, replies };
}

// GET DASHBOARD DATA AND GRAPH HISTORY
function getDashboardData() {
  const sheet = getSheet();
  const data = sheet.getDataRange().getValues();
  const leads = [];

  for (let i = 1; i < data.length; i++) {
    if (!data[i][2]) continue;
    leads.push(readLead(data[i], i + 1));
  }

  const activity = getActivitySheet().getDataRange().getValues();
  const now = new Date();
  const days = [];
  const sentByDay = {};
  const repliesByDay = {};
  const templates = {};

  for (let i = 6; i >= 0; i--) {
    const day = new Date(now);
    day.setDate(day.getDate() - i);
    const key = Utilities.formatDate(
      day, Session.getScriptTimeZone(), "yyyy-MM-dd"
    );
    days.push({
      key,
      label: Utilities.formatDate(
        day, Session.getScriptTimeZone(), "EEE"
      )
    });
    sentByDay[key] = 0;
    repliesByDay[key] = 0;
  }

  for (let i = 1; i < activity.length; i++) {
    const [date, email, company, event, template] = activity[i];
    if (!(date instanceof Date)) continue;

    const key = Utilities.formatDate(
      date, Session.getScriptTimeZone(), "yyyy-MM-dd"
    );

    if (event === "Sent" && key in sentByDay) sentByDay[key]++;
    if (event === "Replied" && key in repliesByDay) repliesByDay[key]++;

    if (event === "Sent") {
      templates[template] = (templates[template] || 0) + 1;
    }
  }

  const drafts = leads.filter(l =>
    ["Draft", "Follow-up Draft"].includes(l.status)
  );

  const sent = leads.filter(l => l.status === "Sent").length;
  const replied = leads.filter(l => l.status === "Replied").length;

  return {
    stats: {
      total: leads.length,
      drafts: drafts.length,
      sent,
      replied,
      positive: 0,
      dailyTarget: CONFIG.DAILY_LIMIT,
      replyRate: sent + replied
        ? (replied / (sent + replied) * 100).toFixed(1)
        : 0
    },
    leads: leads.slice(-100).reverse().map(l => ({
      name: l.name,
      company: l.company,
      email: l.email,
      status: l.status,
      template: l.template,
      draftId: l.draftId,
      followUpDate: l.followUpDate
        ? new Date(l.followUpDate).toLocaleDateString()
        : ""
    })),
    drafts: drafts.map(l => ({
      name: l.name,
      company: l.company,
      email: l.email,
      template: l.template,
      draftId: l.draftId,
      status: l.status
    })),
    graph: {
      labels: days.map(d => d.label),
      sent: days.map(d => sentByDay[d.key]),
      replies: days.map(d => repliesByDay[d.key])
    },
    templateStats: templates
  };
}
