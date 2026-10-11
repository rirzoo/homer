# homer

Read, send and organise mail from several accounts in one terminal command, including **institute / work mail on Microsoft 365 (Outlook)**.

```
$ homer list -a inst -n 3
      1 * 2026-10-11 06:52  Training & Placement          Internship drive: registrations open
      2 * 2026-10-10 23:53  Academic Office               Mid-sem timetable
      3   2026-10-10 20:46  Library                       Book due reminder
$ homer read -a inst 2 --save ~/Downloads
$ homer send -a gmail --to prof@uni.edu -s "Assignment 3" -b "Please find it attached." --attach a3.pdf
```

- **Multiple accounts:** Gmail (or any IMAP/SMTP server) and Microsoft 365 / Outlook side by side.
- **Mail:** list, search, read (with attachments), send (with attachments), drafts, folders, move, mark read/unread.
- **Doesn't change your unread state:** `read` never marks a message as read; only `mark` does.
- **Agent-friendly:** plain text in and out, so AI coding agents (Claude Code etc.) can read and send mail for you through the same commands.
- **No dependencies:** Python standard library only. Passwords and tokens live in your system keyring, never in files.

## Requirements

- Python 3.11+
- Linux with `secret-tool` (libsecret): Fedora `sudo dnf install libsecret`, Debian/Ubuntu `sudo apt install libsecret-tools`

## Install

```sh
pipx install git+https://github.com/rirzoo/homer.git
# or
git clone git@github.com:rirzoo/homer.git && cd homer && pip install --user .
```

You can also run it without installing, from the repo: `python3 -m homer ...`

## Setup

Accounts live in `~/.config/homer/accounts.toml` (override with `$HOMER_CONFIG`). Each account has a short name you pass with `-a`:

```toml
default = "gmail"            # used when -a is omitted

[accounts.gmail]
email = "you@gmail.com"

[accounts.inst]
email = "you@institute.edu"
provider = "outlook"
tenant = "institute.edu"     # your org's domain or tenant ID
client_id = "00000000-0000-0000-0000-000000000000"
```

Then link each account with `homer login -a <name>` (details below) and check with `homer list -a <name>`.

### Linking a Gmail account

Gmail needs an **app password** (a 16-character password just for homer):

1. Turn on 2-Step Verification: <https://myaccount.google.com/signinoptions/twosv>
2. Create an app password: <https://myaccount.google.com/apppasswords>
   (If it says *"The setting you are looking for is not available"*, 2-Step Verification is off, or the account uses Advanced Protection.)
3. In a normal terminal run `homer login -a gmail` and paste it. It goes straight into your keyring.

**Other IMAP/SMTP providers** (Fastmail, Zoho, a university mail server, ...) work the same way. Add the server names:

```toml
[accounts.work]
email = "you@example.com"
imap = "imap.example.com"    # port 993, SSL      (imap_port to override)
smtp = "smtp.example.com"    # port 587, STARTTLS (smtp_port to override)
```

### Linking an institute / Microsoft 365 account

Microsoft 365 doesn't accept passwords from third-party apps, so homer signs in with OAuth through Microsoft Graph. That needs a **client ID**, which identifies the app you sign in through:

**Option A: register your own app** (many organisations let users do this):

1. Open <https://entra.microsoft.com> → *App registrations* → *New registration*. Name it `homer`, keep "Accounts in this organizational directory only", and leave Redirect URI empty.
2. *Authentication* → *Advanced settings* → **Allow public client flows: Yes** → Save.
3. *API permissions* → *Add a permission* → *Microsoft Graph* → *Delegated* → `Mail.ReadWrite` and `Mail.Send`.
4. From *Overview*, copy the **Application (client) ID** into `client_id` and the **Directory (tenant) ID** (or your mail domain) into `tenant`.

**Option B: ask your IT department.** If you can't register apps, or sign-in says *"Need admin approval"*, your organisation controls which apps may access mail. Ask IT to approve your registration, or to give you a client ID they allow. Please don't work around your organisation's policy. If IT gives you a pre-approved app that only works with its preconfigured permissions, add:

```toml
scope = "https://graph.microsoft.com/.default offline_access"
```

Then sign in with:

```sh
homer login -a inst
```

It prints a code and a URL. Open the URL in any browser, enter the code, and sign in as usual (MFA included). homer keeps a refresh token in your keyring and renews it automatically.

## Usage

```
homer accounts                                   list configured accounts
homer login   -a ACCT                            link an account
homer folders -a ACCT                            list folders
homer list    [-a ACCT] [-f FOLDER] [-n 20] [--unread] [--search QUERY]
homer read    ID [-a ACCT] [-f FOLDER] [--save DIR]
homer send    [-a ACCT] --to ADDR [--cc ADDR] [--bcc ADDR] -s SUBJECT [-b BODY] [--attach FILE ...] [--draft]
homer mkdir   FOLDER [-a ACCT]
homer move    ID... --to FOLDER [-a ACCT] [-f FROM]
homer mark    read|unread ID... [-a ACCT] [-f FOLDER]
```

`--to`, `--cc`, `--bcc` and `--attach` can be repeated. Without `-b`, the body is read from stdin (`homer send ... < body.txt`). `--draft` saves to Drafts instead of sending.

Provider differences:

| | Gmail / IMAP | Outlook / Microsoft 365 |
|---|---|---|
| Message `ID` | UID shown by `list` (stable) | `#` from your **most recent** `list` on that account (or a full Graph message id) |
| `--search` | IMAP SEARCH: `'FROM "bob" SINCE 1-Oct-2026'` | Outlook search (KQL): `'from:bob subject:exam'` |
| Folder names | as `homer folders` shows them, e.g. `"[Gmail]/Sent Mail"`, `"[Gmail]/Bin"` | display name, or `inbox`, `sentitems`, `drafts`, `deleteditems`, `junkemail`, `archive` |
| Delete | `move ID --to "[Gmail]/Trash"` (`[Gmail]/Bin` in some regions) | `move ID --to deleteditems` |
| Limits | | max 4 MB per message incl. attachments; `mkdir` creates top-level folders |

Outlook numbers are reassigned on every `list`, so run `list` on the folder right before `read`/`move`/`mark`.

## Help & troubleshooting

- `homer --help` and `homer <command> --help` show all options.
- **`AADSTS...` error, or "Need admin approval" when signing in:** your organisation hasn't approved the app. See Option B above.
- **`graph 403`:** the token lacks mail permissions. Check the app's API permissions, or set `scope` as above, then run `homer login` again.
- **Gmail `AUTHENTICATIONFAILED`:** use an app password, not your normal password, and make sure it was created on the same account as `email`.
- **`password prompt needs a real terminal`:** run `homer login` in a normal terminal window, not through a tool that captures input.
- **`secret-tool not found`:** install libsecret (see Requirements).

Stuck, found a bug, or want a feature (replies, macOS keychain, ...)? **[Open an issue](https://github.com/rirzoo/homer/issues)** with the command you ran and the full error. Remove your email address and any tokens first. Pull requests are welcome.

## Development

```sh
python3 test_homer.py      # offline self-check of message building/parsing
```

Code layout: `homer/cli.py` (commands), `homer/gmail.py` (IMAP/SMTP), `homer/outlook.py` (Microsoft Graph + OAuth), `homer/common.py` (config, keyring, MIME helpers).

## License

[MIT](LICENSE)
