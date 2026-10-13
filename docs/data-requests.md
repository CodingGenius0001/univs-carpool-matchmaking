# Handling privacy requests and suspensions

Requests arrive at the legal contact address shown in the Terms and Privacy
Policy (`LEGAL_CONTACT_EMAIL`, default `legal-campus2air@proton.me`). The
Privacy Policy promises a response within **45 days**. Keep every request and
your reply in that mailbox; it is your record that you handled it.

## 0. Routine (once a month)

- **Last weekend of each month**: read the legal-campus2air@proton.me inbox
  and answer anything new. Privacy requests must be answered within 45 days,
  which a monthly check covers.
- Campus2Air does not take complaints about other users (the Terms say so),
  so there is nothing else to review. If someone emails about a dangerous
  user anyway, point them to 911/the police; you may suspend the account
  from `/admin` → Students if you choose to, but you don't have to.
- Keep the published address exactly `legal-campus2air@proton.me`: dispute
  notices, arbitration opt-outs and the 45-day privacy deadline depend on it.
  Turning on Proton's phone notifications for that inbox is an easy way not
  to miss anything.

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
3. Kept on purpose (and said so in the Privacy Policy): any suspension
   record, carpool summaries with no personal data, and anything
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
```

## 4. Correction request

A student's saved phone number is replaced whenever they enter a different one
while creating or joining a carpool. To fix it for them, in the TiDB SQL editor:

```sql
UPDATE users SET phone = '+1 (951) 555 1234' WHERE user_email = 'name@ucr.edu';
```

Names come from their Google account; ask them to change it there and sign in
again.

## 5. Suspending an account (optional)

`/admin` → Students → **Suspend** signs the person out, blocks sign-in and
removes them from their carpools (carpools they organize are closed and the
riders are told). Lift it from **Suspended accounts** at the bottom of the
Students section. Use it only if you want to; nothing promises it.

## 6. Terms versions

Every version of the Terms is archived as a PDF in `docs/terms-archive/`. When
the Terms change: bump `TERMS_VERSION` / `TERMS_EFFECTIVE` in `app.py`,
regenerate `docs/campus2air-tos.pdf` from `/eula`, and copy it to
`docs/terms-archive/campus2air-tos-v<version>.pdf`. `users.terms_version` and
`terms_accepted_at` show which version each person accepted and when.
