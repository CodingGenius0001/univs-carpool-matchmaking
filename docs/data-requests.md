# Handling privacy requests, reports and suspensions

Requests arrive at the legal contact address shown in the Terms and Privacy
Policy (`LEGAL_CONTACT_EMAIL`, default `legal-campus2air@proton.me`). The
Privacy Policy promises a response within **45 days**. Keep every request and
your reply in that mailbox; it is your record that you handled it.

## 0. Routine (once a month)

- **Last weekend of each month**: open `/admin` and clear the Reports section
  (every open report gets a decision and a short note), then read the
  legal-campus2air@proton.me inbox and answer anything new.
- The site tells students that reports are reviewed about once a month and
  are not monitored in real time, and sends emergencies to 911/police, so a
  monthly check keeps that promise. Privacy requests must be answered within
  45 days, which a monthly check also covers.
- If you happen to see a report sooner (for example the "open safety
  reports" banner when you visit `/admin`) and it describes a threat or
  harassment, suspending right away is fine; it can be undone.
- Your notes on resolved reports are the log; nothing else to keep.
- Keep the published address exactly `legal-campus2air@proton.me`: dispute
  notices, arbitration opt-outs and the 45-day privacy deadline depend on it.
  Turning on Proton's email forwarding or phone notifications for that inbox
  is an easy way not to miss anything.

## 1. Confirm who is asking

Only act on a request about an account when it comes from that account's
`@ucr.edu` address, or when you have replied to that address and the person
confirmed from it. Never act on a request that only names someone else.

## 2. Deletion request

1. Open `/admin` → **Students**, search for the email, and click **Delete data**.
2. This deletes their profile (name, phone, Terms record), notifications, join
   history and activity-log entries (and blanks other log entries that mention
   their email), and removes them from their carpools. Carpools they organize
   are closed and the other riders get a notification.
3. Kept on purpose (and said so in the Privacy Policy): safety reports and
   suspension records, carpool summaries with no personal data, and anything
   the law requires.
4. Reply to the person confirming the deletion and the date.

If they sign in again later, a new account is created (they must accept the
Terms again).

## 3. Access ("right to know") request

Reply with what the Privacy Policy lists, filled in from the database. In the
TiDB SQL editor (replace the email):

```sql
SELECT * FROM users          WHERE user_email = 'name@ucr.edu';
SELECT * FROM party_members  WHERE user_email = 'name@ucr.edu';
SELECT * FROM carpools       WHERE creator_email = 'name@ucr.edu';
SELECT * FROM notifications  WHERE user_email = 'name@ucr.edu';
SELECT * FROM activity_log   WHERE user_email = 'name@ucr.edu';
SELECT * FROM reports        WHERE reporter_email = 'name@ucr.edu';
```

Reports *about* them are their personal information too, but withhold or
summarize them when sharing would identify or endanger the person who
reported (the Privacy Policy says so).

## 4. Correction request

A student's saved phone number is replaced whenever they enter a different one
while creating or joining a carpool. To fix it for them, in the TiDB SQL editor:

```sql
UPDATE users SET phone = '+1 (951) 555 1234' WHERE user_email = 'name@ucr.edu';
```

Names come from their Google account; ask them to change it there and sign in
again.

## 5. Safety reports

1. New reports appear at the top of `/admin` ("open safety reports") and in the
   **Reports** section. Review them on the monthly schedule in section 0.
2. If someone describes an emergency or a crime, tell them to contact 911 /
   the police. Campus2Air cannot investigate crimes.
3. Decide, then click **Mark resolved** with a short note of what you did (for
   example "warned by email", "no action: duplicate", "suspended"). The note is
   the complaint log; write it as if a judge will read it.
4. **Suspend** when a report describes harassment, threats, unsafe driving,
   charging for profit, or when the same person gets repeated reports. A
   suspended account is signed out, cannot sign in, and is removed from its
   carpools. Lift it from **Suspended accounts** if it was a mistake.

## 6. Terms versions

Every version of the Terms is archived as a PDF in `docs/terms-archive/`. When
the Terms change: bump `TERMS_VERSION` / `TERMS_EFFECTIVE` in `app.py`,
regenerate `docs/campus2air-tos.pdf` from `/eula`, and copy it to
`docs/terms-archive/campus2air-tos-v<version>.pdf`. `users.terms_version` and
`terms_accepted_at` show which version each person accepted and when.
