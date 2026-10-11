# Homer TUI: how it's meant to work

This is the design of `homer tui`, which is also what `homer` opens with no command. The wireframe of each window, with the reasoning behind each choice, is in [FRAME.md](FRAME.md), and this file cites its decisions as D1 to D21 and its windows as W1 to W21.

The TUI is for reading and handling your own mail by keyboard. The CLI stays the tool for setup, scripting and agents. Where the two overlap, they act on the same server state, so a change made in one shows up in the other.

## A normal session

1. `homer` opens the default account's Mailbox, and `homer -a inst` opens another account's. The cached list shows at once and syncs in the background (W1, W3).
2. Skim with `j`/`k`. The preview on the right follows the cursor, and a conversation counts as read once it has been in the preview for 2 seconds (D18).
3. Handle each conversation as you reach it: `r` replies, `e` archives, `d` deletes, `M` moves it to one of your folders, and moving on leaves it where it is. Every action shows its result straight away, and `u` undoes it (D15, D16).
4. For something long, `Enter` opens the conversation full width (W5). Links and attachments there are numbered, so `o` opens a link and `s` saves attachments (W6).
5. `c` opens the compose form. Fill in `To` (with autocomplete) and `Subject`, and `Enter` opens your editor for the body. Save and quit, check the form, and press `ctrl-s` (W7, W8).
6. The mail then appears at the top of Mailbox counting down from 10 seconds. It shows `↑` while sending and `✓` once sent, and the status line names the subject and the recipient. `u` during the countdown takes it back (W10).
7. To check the other account, `f` opens the folder pane. `Enter` on `inst` and then `Enter` on its Mailbox switches to it, and the pane closes on its own (W2).
8. `/from:kavitha has:attachment` searches this folder, and `Tab` widens the search to the whole account (W13).
9. `q` quits. A mail still counting down is sent first (W10).

## The folder model

Every account shows the same four Homer folders, plus any you add (D4):

| Folder | What it shows | Where moves into it go | Can be remapped |
|---|---|---|---|
| **Mailbox** | Received and sent mail: your inbox and your sent folder together | INBOX | yes |
| **Drafts** | The account's drafts | the account's Drafts | no, found automatically |
| **Archive** | Everything from its sources that isn't in Mailbox or one of your folders (D5) | the archive target you picked (W16) | yes |
| **Trash** | The account's trash | the account's Trash | no, found automatically |
| *your folders* | Whatever folders or labels you feed into them | the one marked ◆ | yes |

Default mapping for a new account:

| | Gmail | Outlook |
|---|---|---|
| Mailbox | `INBOX`, `[Gmail]/Sent Mail` | `inbox`, `sentitems` |
| Drafts | `[Gmail]/Drafts` | `drafts` |
| Archive | `[Gmail]/All Mail` (archiving removes the Inbox label) | `archive` |
| Trash | `[Gmail]/Trash` or `[Gmail]/Bin` | `deleteditems` |

Rules:
- Each folder or label on the server feeds at most one Homer folder (D6).
- Your folders belong to one account. inst can have a Travels folder and gmail can have a different Travels folder, or none.
- Archiving a conversation moves every mail in it out of Mailbox, your own sent copies included, into Archive's target. On Gmail this takes the Sent label off your copies, so they leave Sent in Gmail's web view too and stay in All Mail (tested over IMAP on 2026-10-11: `UID MOVE` from `[Gmail]/Sent Mail` to `[Gmail]/All Mail` removes the Sent label). On Outlook they move from Sent Items to Archive (tested over Graph on 2026-10-11: a move from `sentitems` to `archive` takes the item out of Sent Items).
- When a conversation's mails sit in sources of several Homer folders (possible with Gmail labels), it's shown in the first match: Mailbox, then your folders in the order the folder pane lists them, then Archive.
- Server folders and labels that don't feed any Homer folder (Spam, for example) don't appear in the TUI. Map them into one of your folders if you want them there.
- The mapping lives in `~/.config/homer/folders.toml`, which the TUI writes (one table per account, the move target listed first). It's kept apart from `accounts.toml` so Homer never rewrites the file you edit by hand. The CLI keeps using the server's folder names (`-f "[Gmail]/Sent Mail"`) and ignores Homer folders.

What the TUI can and can't change:

| Change | TUI | CLI |
|---|---|---|
| Create, rename or delete a Homer folder | yes (W15, W17) | no |
| Choose what feeds Mailbox, Archive or your folders | yes (W16) | no |
| Create a folder or label on the server | no | `homer mkdir` |
| Rename or delete a folder or label on the server | no | no (use webmail) |

## Writing and sending

- Compose is a form (W7). The `From`, `To`, `Cc`, `Bcc` and `Subject` fields are typed in the TUI, and the body is written in `$VISUAL`, falling back to `$EDITOR` and then `vi`. Addresses autocomplete from people you've exchanged mail with on that account (W8).
- The body is read-only in the form, and only `b` reopens it in the editor (D9).
- The form has a field mode, where typing fills in the field, and a command mode behind `Esc` with single-key commands (`s` send, `b` body, `a` attach, `d` draft, `q` close, `D` discard). `ctrl-s` sends from either mode.
- Before sending, Homer checks that there's at least one recipient, every address looks valid, every attachment still exists, the body isn't empty (a forward may have none), and on Outlook the total is 4 MB or less. A missing subject asks once.
- A mail being sent gets its own row in Mailbox (W10), or joins its conversation for a reply, with `↑ 10s` counting down, then `↑` sending, `✓` sent or `!` failed. The status line says `✓ Sent "‹subject›" to ‹name›`. A failed mail stays in the list with `!` and reopens on `Enter`. If you quit with it still unsent, Homer saves it to Drafts (D12, D13).
- `u` during the 10-second countdown cancels the send and reopens the mail.
- Your text is kept in a file under `~/.cache/homer/drafts` from the moment you write it until it's sent or saved, so nothing you write is lost by accident. It reaches the server's Drafts folder when you close the form. `q` closes and keeps the draft, and `D` discards it to Trash with undo (D14).
- A reply keeps the original's account, fills in the recipients and the `Re:` subject, and attaches the quoted original below your text, folded and read-only (W9).
- Every mail sent or drafted from the TUI ends your text with "Sent with homer", where *homer* links to https://github.com/rirzoo/homer. It's shown locked in the form, isn't part of the editor text, and can't be turned off in the TUI. Reopening a draft doesn't add it twice (D10).
- TUI mail goes out as HTML plus a plain-text copy. The body you type is plain text. Homer turns blank-line paragraphs into paragraphs and bare URLs into links, and the plain-text copy spells out the footer link: `Sent with homer (https://github.com/rirzoo/homer)` (D11).

## Features

### Reading

- Two panes, list and preview, by default. The folder pane opens with `f` (D1, D2).
- List rows are conversations, with a mail count. `T` switches to one row per mail (D3).
- Unread rows are marked `●` and bold, rows with attachments `+`, and a conversation you started with no reply yet `To ‹name›`.
- Relative dates (`2m`, `14:02`, `Mon`, `3 Oct`).
- In the conversation view, quoted text is dimmed, older mails collapse, and links and attachments are numbered.
- Attachments can be saved one at a time or all at once to `~/Downloads`, or opened in their default app.
- `v` opens an HTML mail in the browser.

### Handling

- Archive, delete, move, mark read or unread, each with one-step undo.
- Select several rows with `x` and act on all of them at once.
- Actions run in the background. If the server refuses, the change is reversed and the status line says why.

### Finding

- One search syntax for every provider: `from:` `to:` `subject:` `is:unread` `is:read` `has:attachment` `before:YYYY-MM-DD` `after:YYYY-MM-DD` and free words. It searches this folder, or the whole account with `Tab` (D19).

### Accounts and folders

- Every account in one folder pane, with Mailbox unread counts for each.
- Homer folders: the four fixed ones plus your own, created and mapped in the TUI.

### Staying current

- The cache shows mail instantly on start, and the background sync puts new mail at the top without moving the cursor.
- The header shows sync state, offline status and sign-in problems.

### Layout

- Wide terminals (100 columns or more) get two or three panes. Narrower ones get one pane at a time. Resizing switches live (W21).
- Colour always comes with a glyph or a word, and `NO_COLOR` is respected (D21).

## Keys

| Key | List (W1) | Conversation (W5) | Folder pane (W2) |
|---|---|---|---|
| `j` `k` `↑` `↓` | move | scroll | move |
| `g` `G` | top / bottom | top / bottom | |
| `space` `b` | scroll the preview | page | |
| `Enter` | open full width | expand or collapse a mail | open folder / expand account |
| `n` `p` | | next / previous mail | `n` new folder |
| `c` | compose | | |
| `r` `R` | reply / reply all | reply / reply all | `R` rename folder |
| `e` | archive | archive | |
| `d` | delete | delete | |
| `D` | | | delete folder |
| `E` | | | edit mapping |
| `m` | read / unread | read / unread | |
| `M` | move | move | |
| `x` | select | | |
| `u` | undo | undo | |
| `/` | search | | |
| `o` `s` `v` | | link / save / browser | |
| `f` | folders | | close |
| `F` | forward | forward | |
| `T` | conversations or single mails | | |
| `+` | 50 older conversations | | |
| `ctrl+r` | sync now (Homer also syncs every 2 minutes) | | |
| `?` | help | help | help |
| `q` `Esc` | quit / clear | back | close |

Compose keys are in W7.

## Limitations

Most of these are deliberate, and most have a way around them through the CLI or webmail.

- No account setup in the TUI. Configure the account and run `homer login -a ACCT` (W4).
- The TUI never changes server folders or labels. Create them with `homer mkdir`. Rename or delete them in webmail (D7).
- Folders don't cross accounts. You can't move a mail from gmail to inst.
- Server folders that aren't mapped are invisible, Spam included, until you map them into a Homer folder.
- No permanent delete. Trash is emptied by the provider's own rules.
- The body is plain text. No bold, lists or inline images. Paragraphs and links are the only formatting.
- The footer can't be removed from TUI mail. `homer send` from the CLI sends without it.
- HTML mail is shown as text. Images aren't shown, and complex layouts need `v` to open them in the browser.
- Undo goes back one step only. Folder deletion asks instead (W17).
- Offline, you can read cached mail but not change it. Changes are refused, not queued.
- Search covers the common filters only. Provider-specific search tricks need `homer list --search` with the provider's own syntax.
- Autocomplete only knows people from your mail. No address book sync.
- There's no mouse support, no desktop notifications and no replying to calendar invites. New mail shows in the header only while the TUI is open.
- Outlook's 4 MB limit per mail still applies. The form shows the running total.
- A conversation shows the mails in that folder's sources. In Mailbox you see the inbox and sent mails of a thread, not older ones already archived.
- Gmail conversations archived in webmail can come back to Mailbox. Webmail leaves your sent copies in Sent, and Mailbox includes Sent. Archive them once from Homer, which moves the sent copies too. See ISSUES.md I2.
- Results from an all-folders search can't be archived when they already sit in Archive's folder (on Gmail every mail is in All Mail). Search the folder itself to act on them.
- Links and attachments open with `xdg-open`, so that part is Linux only, like the keyring.
- Address suggestions are ranked by how often you've mailed someone on that account, not by how recently.

## Open questions

These decisions have a default but still need your confirmation. Known issues that are logged but not being worked on are in [ISSUES.md](ISSUES.md).

1. The send delay is 10 seconds. A longer one gives more time to undo, and a shorter one brings the `✓` sooner.
2. Conversations are the default view (D3). `T` switches to single mails either way, so this only decides which one you get on start.
3. Two seconds in the preview marks a mail read (D18). It could be anywhere from 1 to 3 seconds, or happen only on `Enter`.
4. Background sync runs every 2 minutes. Still open: whether accounts you aren't viewing should sync too, which is what keeps their unread counts in the folder pane up to date.
