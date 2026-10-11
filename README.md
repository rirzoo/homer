# homer

homer is a terminal mail client for people with more than one inbox. It handles Gmail, any IMAP/SMTP server, and institute or work mail on Microsoft 365 (Outlook). It started at IIT Patna, and IITP accounts have their own setup section below.

```
$ homer list -a inst -n 3
      1 * 2026-10-11 06:52  Training & Placement          Internship drive: registrations open
      2 * 2026-10-10 23:53  Academic Office               Mid-sem timetable
      3   2026-10-10 20:46  Library                       Book due reminder
$ homer read -a inst 2 --save ~/Downloads
$ homer send -a gmail --to prof@uni.edu -s "Assignment 3" -b "Please find it attached." --attach a3.pdf
```

You can list, search and read mail, save attachments, send mail with attachments, reply in the same thread, save drafts, create folders, move messages and mark them read or unread. All your accounts sit side by side and you pick one with `-a`.

Reading a message leaves it unread. Only `mark` changes that, so you (or an AI agent like Claude Code working for you) can look through mail without disturbing it. Every command takes and prints plain text, which is why agents can use it.

homer uses only the Python standard library. Passwords and tokens are stored in your system keyring, not in files.

## Requirements

- Python 3.11+
- Linux with `secret-tool` (libsecret): Fedora `sudo dnf install libsecret`, Debian/Ubuntu `sudo apt install libsecret-tools`

## Install

homer installs for your user only, into `~/.local/bin`. You don't need `sudo` for homer itself, and since your config and keyring are per user anyway, there's no reason to install it system wide.

1. Install pipx, and git if you don't have it:

   ```sh
   sudo dnf install pipx git        # Fedora
   sudo apt install pipx git        # Debian / Ubuntu
   ```

2. Install homer:

   ```sh
   pipx install git+https://github.com/rirzoo/homer.git
   ```

3. Put `~/.local/bin` on your PATH, then open a new terminal:

   ```sh
   pipx ensurepath
   ```

   To do it by hand instead, add this line to `~/.bashrc` (or `~/.zshrc` if you use zsh):

   ```sh
   export PATH="$HOME/.local/bin:$PATH"
   ```

4. Run `homer --help`. If you get `command not found`, your shell hasn't picked up the new PATH yet. Open a new terminal or run `source ~/.bashrc`.

You can use pip instead of pipx: `git clone https://github.com/rirzoo/homer.git && cd homer && pip install --user .`. Newer Ubuntu and Debian releases refuse that with an `externally-managed-environment` error, so stick with pipx there. To try homer without installing it, run `python3 -m homer ...` from the cloned folder.

### Updating and removing

```sh
pipx reinstall homer-mail          # get the latest version
pipx uninstall homer-mail          # remove homer
secret-tool clear service homer    # delete the passwords and tokens homer stored
```

Your config in `~/.config/homer/` stays until you delete it yourself.

## Setup

homer doesn't create a config for you. Make the folder with `mkdir -p ~/.config/homer`, then create `~/.config/homer/accounts.toml` in any text editor (set `$HOMER_CONFIG` if you want it somewhere else). Each account gets a short name that you pass with `-a`:

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

Gmail needs an app password, a 16-character password that only homer uses:

1. Turn on 2-Step Verification: <https://myaccount.google.com/signinoptions/twosv>
2. Create an app password: <https://myaccount.google.com/apppasswords>
   (If it says *"The setting you are looking for is not available"*, 2-Step Verification is off, or the account uses Advanced Protection.)
3. In a normal terminal run `homer login -a gmail` and paste it. It goes straight into your keyring.

Other IMAP/SMTP providers (Fastmail, Zoho, a university mail server) work the same way once you add the server names:

```toml
[accounts.work]
email = "you@example.com"
imap = "imap.example.com"    # port 993, SSL      (imap_port to override)
smtp = "smtp.example.com"    # port 587, STARTTLS (smtp_port to override)
```

### Linking an IIT Patna account

IITP mail runs on Microsoft 365 and only lets Microsoft's own apps sign in, so homer signs in as the Microsoft Office app using that app's public client ID. IITP's IT department has said this is fine for IITP accounts. Add this to your config, with your own address:

```toml
[accounts.inst]
email = "you@iitp.ac.in"
provider = "outlook"
tenant = "iitp.ac.in"
client_id = "d3590ed6-52b3-4102-aeff-aad2292ab01c"   # Microsoft Office
scope = "https://graph.microsoft.com/.default offline_access"
```

Keep the `scope` line as it is. The Office app can't ask for mail permissions one by one (Microsoft answers with `AADSTS65002`), but with `.default` it gets the mail read and send permissions it already has.

Then run `homer login -a inst`. It prints a URL and a code. Open the URL in any browser, enter the code, and sign in with your IITP account as usual, MFA included. homer stores a refresh token in your keyring and renews it on its own, so you only do this once. Check that it worked with `homer list -a inst`.

This approval covers IITP only. If you're somewhere else, use the next section.

### Linking another institute / Microsoft 365 account

Microsoft 365 doesn't accept passwords from third-party apps, so homer signs in with OAuth through Microsoft Graph. For that you need a client ID, which tells Microsoft which app you're signing in through. There are two ways to get one.

#### Option A: register your own app

Many organisations let users do this.

1. Open <https://entra.microsoft.com>, go to *App registrations* and click *New registration*. Name it `homer`, keep "Accounts in this organizational directory only", and leave Redirect URI empty.
2. Under *Authentication*, open *Advanced settings*, set *Allow public client flows* to Yes, and save.
3. Under *API permissions*, click *Add a permission*, choose *Microsoft Graph*, then *Delegated*, and add `Mail.ReadWrite` and `Mail.Send`.
4. On the *Overview* page, copy the Application (client) ID into `client_id` and the Directory (tenant) ID into `tenant`. Your mail domain also works for `tenant`.

#### Option B: ask your IT department

If you can't register apps, or sign-in says *"Need admin approval"*, your organisation decides which apps can access mail. Ask IT to approve your registration or to give you a client ID they allow, and please don't try to get around their policy. Some pre-approved apps only work with their preconfigured permissions. If IT gives you one of those, add:

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
homer reply   ID [-a ACCT] [-f FOLDER] [-b BODY] [--all] [--attach FILE ...] [--draft]
homer mkdir   FOLDER [-a ACCT]
homer move    ID... --to FOLDER [-a ACCT] [-f FROM]
homer mark    read|unread ID... [-a ACCT] [-f FOLDER]
```

`--to`, `--cc`, `--bcc` and `--attach` can be repeated. Without `-b`, the body is read from stdin (`homer send ... < body.txt`). `--draft` saves to Drafts instead of sending.

`reply` answers the sender of message `ID` (add `--all` to include everyone else on it). It adds "Re:" to the subject, quotes the original under your text, and sets the headers mail apps use to keep the reply in the same conversation. Replying to a message you sent yourself goes to its original recipients, which is handy for a follow-up.

Provider differences:

| | Gmail / IMAP | Outlook / Microsoft 365 |
|---|---|---|
| Message `ID` | UID shown by `list` (stable) | `#` from your most recent `list` on that account (or a full Graph message id) |
| `--search` | IMAP SEARCH: `'FROM "bob" SINCE 1-Oct-2026'` | Outlook search (KQL): `'from:bob subject:exam'` |
| Folder names | as `homer folders` shows them, e.g. `"[Gmail]/Sent Mail"`, `"[Gmail]/Bin"` | display name, or `inbox`, `sentitems`, `drafts`, `deleteditems`, `junkemail`, `archive` |
| Delete | `move ID --to "[Gmail]/Trash"` (`[Gmail]/Bin` in some regions) | `move ID --to deleteditems` |
| Limits | | max 4 MB per message incl. attachments; `mkdir` creates top-level folders |

Outlook numbers are reassigned on every `list`, so run `list` on the folder right before `read`/`move`/`mark`.

## Help & troubleshooting

- `homer --help` and `homer <command> --help` show all options.
- `AADSTS65002` on an IITP account means the `scope` line is missing from your config.
- An `AADSTS...` error or "Need admin approval" at sign-in means your organisation hasn't approved the app. See Option B.
- `graph 403` means the token doesn't have mail permissions. Check the app's API permissions or set `scope` as shown above, then run `homer login` again.
- Gmail `AUTHENTICATIONFAILED` usually means you used your normal password. homer needs an app password, created on the same account as `email`.
- `password prompt needs a real terminal` shows up when something else is capturing input. Run `homer login` in a normal terminal window.
- `secret-tool not found` means libsecret isn't installed (see Requirements).

If you're stuck or something's broken, [open an issue](https://github.com/rirzoo/homer/issues) with the command you ran and the full error, minus your email address and any tokens. Feature requests (macOS keychain support, say) and pull requests are welcome too.

## Development

```sh
python3 test_homer.py      # offline self-check of message building/parsing
```

Code layout: `homer/cli.py` (commands), `homer/gmail.py` (IMAP/SMTP), `homer/outlook.py` (Microsoft Graph + OAuth), `homer/common.py` (config, keyring, MIME helpers).

## License

[MIT](LICENSE)
