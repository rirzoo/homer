# Homer TUI wireframes

Every window the TUI can show, and why it looks the way it does. The Textual TUI in `homer/tui.py` was built from it. How the windows fit together in daily use, along with features and limits, is in [TUI.md](TUI.md).

How to read the wireframes:

- They are drawn at 100 columns. Wider terminals give the extra width to the list and the preview.
- `>` at the start of a row marks the cursor row. On screen that row is drawn in reverse video and the `>` isn't there.
- `[ ]` and `[x]` are checkboxes. `▾` and `▸` are an expanded and a collapsed group.
- Text in `‹angle quotes›` stands for content that changes.

## Decisions

| # | Decision | Why | Window |
|---|---|---|---|
| D1 | Two panes by default (list and preview). The folder pane only opens with `f` and closes once you pick a folder. | Folders should only show when you want them. You use the preview on every keystroke and the folder pane now and then. | W1, W2 |
| D2 | Other accounts are reached from the folder pane. There's no key per account. | Account and folder are one choice, so there's one place to change where you are. | W2 |
| D3 | A list row is a conversation: a mail and all its replies. | Mailbox mixes received and sent mail, so with one row per mail your replies would sit away from what they answer. `T` switches to one row per mail if you prefer that. | W1, W5 |
| D4 | Four fixed Homer folders on every account: Mailbox, Drafts, Archive, Trash. You can add your own folders to each account, and they belong to that account. | It's the folder model you asked for. With the same four everywhere, your hands learn one layout whatever the provider calls things. | W2, W15 to W17 |
| D5 | Archive is the catch-all: it shows everything from its sources that isn't already in Mailbox or one of your own folders. | Gmail's archive (All Mail) holds everything, so without this rule every conversation would show up twice. | W2, W16 |
| D6 | Each provider folder or label feeds at most one Homer folder. | A conversation then lives in exactly one place, and moving it has an obvious meaning. | W16 |
| D7 | Homer folders are created and mapped in the TUI. Provider folders and labels are only created with the CLI (`homer mkdir`). | The split you asked for. The TUI never changes the account's own structure, so a keypress in the TUI can't rearrange the folders you see in webmail. | W15 to W17 |
| D8 | Compose is a form: headers are typed in the TUI with autocomplete, and the body is written in `$EDITOR`. | Your call. Headers are short and autocomplete helps with them. The body is long enough to want your own editor. | W7, W8 |
| D9 | The body is never editable in the compose window. Only `b` opens it in `$EDITOR`. | The old compose let the body be changed by accident. Now no stray key in the form can touch the text you wrote. | W7 |
| D10 | Every mail sent or drafted from the TUI ends with "Sent with homer", with the word homer linking to https://github.com/rirzoo/homer. It's shown locked in the form and you can't edit or remove it. | You asked for it on every mail, with no exceptions. The footer isn't in the editor buffer, so nothing you do there can delete it. | W7 |
| D11 | Mail from the TUI goes out as HTML plus a plain-text copy. The body you type is plain text, and Homer turns it into simple HTML (paragraphs, links). | The footer needs a real link on the word "Homer", and that takes HTML. The plain-text copy spells the link out for mail apps that don't show HTML. | W7 |
| D12 | Sending puts the mail in the list right away as its own row, with a visible state: waiting, sending, sent or failed. The status line names the subject and the recipient. | With the old TUI you couldn't tell whether a mail had gone out, because the only sign was a footer that showed its `To:`. Now the state sits on the mail's own row. | W10 |
| D13 | Sending waits 10 seconds, and `u` cancels it during that time and reopens the mail. | It replaces the "are you sure" prompt with a window in which you can still take the mail back. | W10 |
| D14 | Leaving compose saves to Drafts. It never throws away what you wrote. Discarding takes its own key and can be undone. | Losing written mail is the worst outcome, and saving costs nothing. | W7, W11 |
| D15 | Actions update the screen immediately and run against the server in the background. There's no "working…" freeze. If the server refuses, the change is reversed and the status line says why. | The old TUI froze on every server call, and that was most of why it felt bad. | all |
| D16 | Undo (`u`) instead of confirmation prompts for archive, delete, move and mark. | Prompts get answered on autopilot, while undo catches the real mistakes. | W1, W19 |
| D17 | Two bottom lines: a status line (what just happened) above a hint line (what you can press). | In the old TUI a status message overwrote the hints, so you lost one or the other. | all |
| D18 | Previewing a conversation for 2 seconds, or opening it, marks it read. Moving past it quickly doesn't. | Moving down the list shouldn't mark everything read, but reading in the preview counts as reading. | W1 |
| D19 | One search syntax (`from:` `to:` `subject:` `is:unread` `has:attachment` `before:` `after:` plus free words), which Homer translates for each provider. | Without it you'd need IMAP syntax for Gmail and KQL for Outlook. | W13 |
| D20 | Below 100 columns the layout falls back to one pane at a time. | Two readable panes need about 100 columns, and squeezing them below that helps neither. | W21 |
| D21 | Colour is never the only signal. Every colour has a glyph or a word next to it, and `NO_COLOR` is respected. | Colour can be off in some terminals, and some people can't tell certain colours apart. | all |

## Screen anatomy

```
 homer · gmail · Mailbox                                         3 unread · synced 2m ago  ← header
──────────────────────────────────────────────┬───────────────────────────────────────────────
                                              │
  list pane                                   │  preview pane                                 ← body
                                              │
──────────────────────────────────────────────┴───────────────────────────────────────────────
 Archived "Leave form" · u undo                                                             ← status
 c new  r reply  e archive  d delete  f folders  / search  ? help                            ← hints
```

- The header shows where you are on the left (Homer · account · folder, plus the search if one is active), and the unread count and sync state on the right. The sync state is one of `synced 2m ago`, `syncing…`, `offline` or `gmail: sign in again (homer login -a gmail)`.
- The status line shows the result of the last thing you did, named by subject and recipient, and how to undo it. Errors are red and start with `!`. The line stays until your next action.
- The hint line shows the 6 to 8 most useful keys for the window you're in, and `?` shows the rest.

Glyphs in the list:

| Glyph | Meaning |
|---|---|
| `●` | unread (the row is also bold) |
| `+` | has attachments |
| `(3)` | number of mails in the conversation |
| `To ‹name›` | conversation you started that has no reply yet (the sender column shows who it went to) |
| `↑ 8s` | waiting to send, seconds left to undo |
| `↑` | sending now |
| `✓` | sent (shown for one sync cycle, then the row is a normal conversation) |
| `!` | failed to send (red) |
| `■` | selected for a group action (`x`) |

Dates: `2m`, `45m` within the hour, `14:02` today, `Mon` this week, `3 Oct` this year, `3 Oct 25` before that.

---

## W1 · Main window: list and preview (default)

```
 homer · gmail · Mailbox                                         3 unread · synced 2m ago
──────────────────────────────────────────────┬───────────────────────────────────────────────
>● Kavitha Das (2)    Trip schedule        2m │ Trip schedule
 ● Dean Academics   + Leave form        14:02 │ Kavitha Das, me · 2 mails
 ● GitHub (4)         PR #2 merged      13:40 │ ───────────────────────────────────────────────
   To Amit            Tickets for Sunday  Mon │ ▸ me                               Sat 10 Oct
   Rahul (5)          Weekend plan        Mon │   Sure, send it over when it's out.
   Amazon           + Your order          Sun │ ───────────────────────────────────────────────
   Library            Books due Friday  3 Oct │ ▾ Kavitha Das                 Sun 11 Oct 14:02
   Hostel Office    + Room allotment    2 Oct │   to me
   Amit (3)           Re: Rent          1 Oct │
   ...                                        │   Hi Arjun,
                                              │
                                              │   The schedule is out. We leave on the
                                              │   19th, see the PDF [1].
                                              │
                                              │   > On Sat, me wrote:
                                              │   > Sure, send it over when it's out.
                                              │
                                              │   [1] schedule.pdf · 120 KB
──────────────────────────────────────────────┴───────────────────────────────────────────────

 c new  r reply  R all  F forward  e archive  d delete  space scroll  Enter open  f folders  ? help
```

What it's for: skimming and handling mail. The list always has the focus, `j`/`k` move it, and the preview follows the cursor.

Keys: `j` `k` move · `g` `G` top/bottom · `space` `b` scroll the preview · `Enter` open full width (W5) · `c` compose · `r` `R` reply / reply all · `e` archive · `d` delete · `m` read/unread · `M` move · `F` forward · `x` select · `u` undo · `/` search · `f` folders · `T` conversations or single mails · `?` help · `q` quit.

Why:
- Keeping the focus on the list means `j`/`k` always mean the same thing. The preview only scrolls with `space`/`b`, so you never have to think about which pane you're in (D1).
- The preview shows older mails in a conversation collapsed to one line and the newest expanded, because the newest is almost always the one you need.
- Quoted text is dimmed, and links and attachments are numbered (`[1]`) so they can be opened or saved by number from W5.

## W2 · Main window with the folder pane (`f`)

```
 homer · gmail · Mailbox                                         3 unread · synced 2m ago
──────────────────┬──────────────────────────────────────┬────────────────────────────────────
 ▾ gmail          │ ● Kavitha Das (2) Trip schedule    2m │ Trip schedule
>  Mailbox      3 │ ● Dean Academics  Leave form    14:02 │ Kavitha Das, me · 2 mails
   Drafts       1 │ ● GitHub (4)      PR #2 merged  13:40 │ ──────────────────────────────────
   Archive        │   To Amit         Tickets for…    Mon │ ▾ Kavitha Das      Sun 11 Oct 14:02
   Trash          │   Rahul (5)       Weekend plan    Mon │   to me
   ─ yours ─      │   Amazon          Your order      Sun │
   Travels      2 │   ...                                 │   Hi Arjun,
   Payments       │                                       │   ...
 ▸ inst        12 │                                       │
                  │                                       │
 + new folder     │                                       │
──────────────────┴──────────────────────────────────────┴────────────────────────────────────

 Enter open  n new folder  E edit mapping  R rename  D delete folder  f/Esc close
```

What it's for: changing folder or account, and managing Homer folders.

Behaviour:
- `f` opens the pane with the focus on it, and the cursor starts on the folder you're in. `f` or `Esc` closes it without changing anything.
- `Enter` on a folder opens it and closes the pane, which takes you back to two panes.
- `Enter` on another account (`▸ inst 12`) expands it and collapses the current one, so `j`/`k` can then pick its folder. Choosing a folder switches the account.
- The number after an account is that account's unread count in Mailbox, from its last background sync.
- Your own folders are listed under `─ yours ─`, below the four fixed ones.

Folder keys: `n` new folder (W15) · `E` edit what feeds the folder (W16; Mailbox, Archive and your own folders) · `R` rename (your folders only) · `D` delete (W17; your folders only).

Why:
- Accounts sit inside the same tree as folders, so switching account is the same move as switching folder (D2). There's no separate account key to remember.
- Only one account is expanded at a time to keep the pane short, and because you work in one account at a time.
- Drafts and Trash can't be remapped. Homer finds them itself, and getting them wrong would hide drafts or lose deleted mail.

## W3 · Loading and empty states

First start with nothing cached:

```
 homer · gmail · Mailbox                                                           syncing…
──────────────────────────────────────────────┬───────────────────────────────────────────────
                                              │
   Loading Mailbox…                           │
                                              │
──────────────────────────────────────────────┴───────────────────────────────────────────────

 f folders  ? help  q quit
```

An empty folder:

```
──────────────────────────────────────────────┬───────────────────────────────────────────────
                                              │
   Nothing in Archive.                        │
                                              │
──────────────────────────────────────────────┴───────────────────────────────────────────────
```

Every later start shows the cached list right away with `syncing…` on the right, and new mail is added at the top without moving your cursor.

Why: you can start reading straight away. The cursor never jumps because mail arrived, or a keypress meant for one row could land on another.

## W4 · No accounts configured

```
 homer
──────────────────────────────────────────────────────────────────────────────────────────────

   No accounts set up yet.

   Add one in your config file, then link it from the command line:

     homer login -a gmail

   See README.md, "Setup".

──────────────────────────────────────────────────────────────────────────────────────────────

 q quit
```

Why: account setup involves app passwords and OAuth device codes, which the CLI already handles. Doing it again in the TUI would duplicate that work for something you do once.

## W5 · Conversation, full width (`Enter`)

```
 homer · gmail · Mailbox › Trip schedule                                           2 mails
──────────────────────────────────────────────────────────────────────────────────────────────
 Trip schedule
 Kavitha Das, me
──────────────────────────────────────────────────────────────────────────────────────────────
 ▸ me → Kavitha Das                                                              Sat 10 Oct
──────────────────────────────────────────────────────────────────────────────────────────────
>▾ Kavitha Das <kavitha@example.com>                                      Sun 11 Oct, 14:02
   to me
   cc Rahul

   Hi Arjun,

   The schedule is out. We leave on the 19th, see the PDF [1]. The hotel list is on the
   booking page [2].

   > On Sat 10 Oct, me wrote:
   > Sure, send it over when it's out.

   Links        [2] https://example.com/trip/plan
   Attachments  [1] schedule.pdf · 120 KB
──────────────────────────────────────────────────────────────────────────────────────────────
 23/41
 r reply  R all  F forward  e archive  s save  o open link  n/p next/prev mail  v browser  q back
```

What it's for: reading long mail and long conversations.

Keys: `j` `k` scroll · `space` `b` page · `n` `p` next/previous mail in the conversation · `Enter` expand or collapse the mail at the cursor · `o` open a link (W6) · `s` save attachments (W6) · `v` open the mail in the browser · `r` `R` `e` `d` `m` `M` as in the list · `q` `Esc` back to the list.

Why:
- Full name and address, `to` and `cc` only show here. The preview keeps them short because you're skimming there.
- Links and attachments are gathered at the bottom of each mail with numbers, so a link buried in a paragraph can still be opened.
- `v` exists because some HTML mail (newsletters, bills) can't be read in a terminal at all.
- After archiving or deleting from here, you go back to the list with the cursor on the next conversation.

## W6 · Attachments and links pickers (`s`, `o`)

```
                ┌ Save attachments ──────────────────────────────────────┐
                │ [x] schedule.pdf                           120 KB      │
                │>[ ] hotels.xlsx                             34 KB      │
                │                                                        │
                │ to ~/Downloads                                         │
                │ space pick  a all  Enter save  O open  Esc cancel      │
                └────────────────────────────────────────────────────────┘
```

```
                ┌ Open link ─────────────────────────────────────────────┐
                │ 1  https://example.com/trip/plan                       │
                │ 2  https://drive.google.com/…/view                     │
                │ Enter or number: open in browser  y copy  Esc cancel   │
                └────────────────────────────────────────────────────────┘
```

After saving, the status line shows `Saved schedule.pdf to ~/Downloads`.

Why: the old TUI's `s` saved every attachment at once. Picking matters when a mail carries 12 scans and you need one. `O` opens the file in its default app without you having to find it in Downloads first. A single attachment or link skips the picker and is acted on directly.

## W7 · Compose

```
 homer · Compose · new mail                                                   text kept 14:05
──────────────────────────────────────────────────────────────────────────────────────────────
  From     gmail · arjun.mehta@gmail.com                                                ▾
  To       Kavitha Das <kavitha@example.com>, ▏
  Cc
  Bcc
  Subject  Trip schedule
──────────────────────────────────────────────────────────────────────────────────────────────
  Attach   [1] notes.pdf · 1.2 MB      [2] timetable.png · 340 KB          total 1.5 MB
──────────────────────────────────────────────────────────────────────────────────────────────
  Body                                                                           b to edit
  ┆ Hi Kavitha,
  ┆
  ┆ Thanks for the schedule. My notes are attached.
  ┆
  ┆ Arjun
  ┆
  ┆ 🔒 Sent with homer                                                  (always added, locked)
──────────────────────────────────────────────────────────────────────────────────────────────

 ctrl+s send  Esc commands
```

Compose has two modes:

- **Field mode** (when the window opens): the cursor is in a header field and typing fills it in. `Tab` and `↓` go to the next field, `Shift-Tab` and `↑` to the previous one. In `To`, `Cc` and `Bcc`, typing opens autocomplete (W8). `ctrl-s` sends from either mode.
- **Command mode** (`Esc`): single keys act on the whole mail. The hint line then shows:

```
 s send  b edit body  a attach  X remove attachment  d save draft  q close (keeps draft)  D discard
```

Rules:
- The body is shown read-only, marked with `┆`. `b` opens it in `$EDITOR`, and the form shows it again once you save and quit (D9).
- The footer is drawn under the body with a lock, and isn't part of the text in the editor. It's added when the mail is saved or sent (D10).
- **From** picks the sending account (default: the account you're in). It's fixed on a reply, which always goes out from the account that received the original.
- `s` and `ctrl-s` check the mail first: at least one recipient, no attachment that has disappeared, no recipient that doesn't look like an address, a body that isn't empty, and on Outlook a total size of 4 MB or less. Problems show on the status line and the cursor goes to the field at fault. Nothing is sent until they're fixed.
- A missing subject asks once: `No subject. s send anyway  Esc go back`.
- Your text is kept in a file under `~/.cache/homer/drafts` from the moment the editor closes, so a crash loses nothing, and the header shows when it was last kept. It goes to the server's Drafts folder when you close the form (`q` or `d`). Saving to the server on every change was dropped: each save makes a new server draft, so the old one would have to be deleted every time.
- `q` closes and keeps the draft (D14). `D` discards: the draft goes to Trash and the status line offers `u undo`.

Why:
- On a new mail, `Enter` in the `Subject` field opens `$EDITOR` for the body, so a whole mail is `c`, recipient, `Tab`, subject, `Enter`, write, save and quit, `ctrl-s`.
- Field mode and command mode exist because single-key commands and a text field can't share the same keys. `Esc` to leave text entry is already habit for a vim user, and the `j`/`k` keys assume one.
- `ctrl-s` works in both modes, so sending never needs a mode switch.

## W8 · Address autocomplete

```
  To       Kavitha Das <kavitha@example.com>, ra▏
           ┌───────────────────────────────────────────────┐
           │>Rahul <rahul@example.com>                      │
           │ Rakesh Menon <rakesh@example.com>              │
           │ Ramya Joshi <ramya@example.com>                │
           └───────────────────────────────────────────────┘
```

- Matches on name or address, anywhere in the word. The people you write to most often come first.
- Suggestions come from people you've exchanged mail with on the sending account.
- `Tab` or `Enter` picks one and adds `, `, `↑` and `↓` move through the list, and `Esc` closes it while keeping what you typed.
- An entry that isn't a complete address is underlined in red and blocks sending until you fix it.

Why: typing a full address is where mistakes come from. Suggestions from your own mail are the addresses you actually use.

## W9 · Reply compose (`r` / `R`)

```
 homer · Reply · Trip schedule                                                text kept 14:07
──────────────────────────────────────────────────────────────────────────────────────────────
  From     gmail · arjun.mehta@gmail.com                                         (fixed)
  To       Kavitha Das <kavitha@example.com>
  Cc       Rahul <rahul@example.com>
  Subject  Re: Trip schedule
──────────────────────────────────────────────────────────────────────────────────────────────
  Attach   (none)                                                                 a to add
──────────────────────────────────────────────────────────────────────────────────────────────
  Body                                                                           b to edit
  ┆ Thanks! See you on the 19th.
  ┆
  ┆ 🔒 Sent with homer                                                  (always added, locked)
  ┆
  ┆ ▸ On Sun 11 Oct, Kavitha Das wrote: (12 lines, quoted)
──────────────────────────────────────────────────────────────────────────────────────────────

 ctrl+s send  Esc commands
```

- `r` opens with the editor already on the body, because the recipients are known. `R` adds everyone else on the mail to `Cc`.
- `To`, `Cc` and `Subject` are filled in but can still be edited.
- The quote of the original is attached below and folded. It isn't in the editor buffer, so you can't change what the other person said by accident.

Why the footer goes above the quote: "every mail ends with Sent with homer" is read as "everything you wrote ends with it". Putting the footer below a 40-line quote would hide it. If you want it literally last, below the quote, that's a one-line rule change. This is logged in [ISSUES.md](ISSUES.md) (I1).

## W10 · After sending: the mail's own row

The moment you send, compose closes and the mail shows at the top of Mailbox:

```
 homer · gmail · Mailbox                                         3 unread · 1 sending
──────────────────────────────────────────────┬───────────────────────────────────────────────
>↑ 8s To Kavitha Das  Trip schedule       now │ Trip schedule
 ● Kavitha Das (2)    Trip schedule        2m │ me → Kavitha Das · waiting to send
 ● Dean Academics   + Leave form        14:02 │ ───────────────────────────────────────────────
   ...                                        │ Thanks for the schedule. My notes are attached.
──────────────────────────────────────────────┴───────────────────────────────────────────────
 Sending "Trip schedule" to Kavitha Das in 8s · u undo
 c new  r reply  e archive  d delete  f folders  ? help
```

The row and the status line then go through these states:

| Row | Status line |
|---|---|
| `↑ 8s` (counting down) | `Sending "Trip schedule" to Kavitha Das in 8s · u undo` |
| `↑` | `Sending "Trip schedule" to Kavitha Das…` |
| `✓` (green) | `✓ Sent "Trip schedule" to Kavitha Das` |
| `!` (red) | `! Not sent: "Trip schedule" to Kavitha Das. ‹reason›. Enter on it to fix` |

- On a reply, the sent mail joins its conversation's row instead of making a new one. That row moves to the top and shows the same glyphs.
- `u` while counting down cancels the send and reopens the mail in compose exactly as it was.
- A failed send reopens in compose on `Enter`, with everything intact. `s` tries again. It isn't put in Drafts straight away, so a retry doesn't leave a stale copy there.
- Quitting while a mail is counting down sends it right away and waits for it, showing `Sending 1 mail before quitting…`. A mail that still fails, or had already failed, is saved to Drafts and the reason is printed after the TUI closes.

Why (D12, D13): not knowing whether a mail had gone out was the worst part of the old TUI. Here the proof sits on the mail itself, named by its subject and recipient, so it can't be confused with another mail's prompt.

## W11 · Leaving compose

`q` in command mode doesn't ask anything. It closes, and the status line says:

```
 Saved "Trip schedule" to Drafts
```

`D` (discard) doesn't ask either:

```
 Discarded "Trip schedule" · u undo
```

Why: neither path can lose your text, so a prompt would only slow you down (D14).

## W12 · Drafts folder

The same as W1. `Enter` on a draft opens it in compose (W7 or W9) instead of the reader. The row shows the recipient and how long ago it was last edited:

```
> To Kavitha Das     Trip schedule      14:05 │ ...
  To (no one yet)    Notes for Monday     Fri │
```

Drafts made elsewhere (webmail, `homer send --draft`) open in the same way. If the body is HTML, Homer shows it as text in the editor and says so on the status line, since the original formatting won't survive the edit.

## W13 · Search (`/`)

```
 homer · gmail · Mailbox · search                                                       ……
──────────────────────────────────────────────────────────────────────────────────────────────
 / from:kavitha has:attachment trip▏                                 this folder · Tab: all
──────────────────────────────────────────────────────────────────────────────────────────────
 from: to: subject: is:unread is:read has:attachment before:2026-10-01 after:2026-09-01 words
```

After `Enter`:

```
 homer · gmail · Mailbox · "from:kavitha has:attachment trip"             4 results
──────────────────────────────────────────────┬───────────────────────────────────────────────
>  Kavitha Das (2)  + Trip schedule        2m │ ...
   Kavitha Das      + Report review    12 May │
   ...                                        │
──────────────────────────────────────────────┴───────────────────────────────────────────────
 
 Enter open  r reply  e archive  / edit search  Esc clear  ? help
```

- `Tab` while typing switches between searching this folder and searching all folders of the account.
- `/` again reopens the bar with the current search, so you can refine it.
- Words that aren't recognised as filters are searched for in the subject and body.
- An invalid filter (`before:yesterdy`) is underlined in red, and nothing is sent to the server.

Why: one syntax across Gmail and Outlook (D19), and the filters are listed under the bar so you don't have to remember them.

## W14 · Move to folder (`M`)

```
                ┌ Move "Leave form" to ──────────────────┐
                │ Mailbox                                │
                │ Archive                                │
                │>Travels                                │
                │ Payments                               │
                │ Trash                                  │
                │                                        │
                │ type to filter  Enter move  Esc cancel │
                └────────────────────────────────────────┘
```

- Only the Homer folders of the current account are listed (folders don't cross accounts).
- Typing filters the list, so `tra` then `Enter` is enough.
- The status line then says `Moved "Leave form" to Travels · u undo`.

Why: a small box over the list instead of a full screen, so you still see what you're moving.

## W15 · New Homer folder (`n` in the folder pane)

```
                ┌ New folder in gmail ───────────────────┐
                │ Name  Travels▏                         │
                │                                        │
                │ Enter next: choose what feeds it       │
                │ Esc cancel                             │
                └────────────────────────────────────────┘
```

`Enter` goes straight to W16 for the new folder. A folder needs at least one source, so cancelling out of W16 doesn't create it.

- The name must be unique within the account and can't be Mailbox, Drafts, Archive or Trash.
- The folder exists only in Homer. Nothing is created on the server (D7).

## W16 · Edit what feeds a Homer folder (`E`)

```
 homer · gmail · folders › Travels
──────────────────────────────────────────────────────────────────────────────────────────────
 Mail in these gmail folders and labels shows in Travels. Moves to Travels go to the one
 marked ◆.

 >[x] ◆ Travels/2026                            34 mails
  [x]   Travels/Treks                           12 mails
  [ ]   Payments
  [ ]   [Gmail]/Spam
  [ ]   [Gmail]/Important
  ─ in other homer folders ─
  [·]   INBOX                                   in Mailbox
  [·]   [Gmail]/Sent Mail                       in Mailbox
  [·]   [Gmail]/All Mail                        in Archive

 To create a new gmail label, use the CLI: homer mkdir NAME -a gmail
──────────────────────────────────────────────────────────────────────────────────────────────

 space pick  t make it where moves go  Enter save  Esc cancel
```

- Folders and labels already feeding another Homer folder are greyed out with `[·]` and show where they are (D6). To move one, remove it from that folder first.
- `◆` marks the folder that moves land in. It defaults to the first one you pick.
- On Mailbox, the defaults are INBOX and Sent (Gmail: `INBOX`, `[Gmail]/Sent Mail`; Outlook: `inbox`, `sentitems`). Moves to Mailbox go to INBOX.
- On Archive, the defaults are `[Gmail]/All Mail` and Outlook's `archive`. Archive is the catch-all (D5), so it only shows conversations that aren't in Mailbox or your own folders.
- Saving refreshes the folder's list.

Why: one screen covers both what goes into Mailbox and Archive and the mapping for your own folders. The CLI hint is there because the TUI deliberately can't create labels (D7).

## W17 · Delete a Homer folder (`D` in the folder pane)

```
                ┌ Delete folder "Travels"? ──────────────────────────────┐
                │ The folder is removed from homer only. Its 46 mails    │
                │ stay on the server, and Travels/2026 and Travels/Treks │
                │ feed Archive again.                                    │
                │                                                        │
                │ y delete  Esc cancel                                   │
                └────────────────────────────────────────────────────────┘
```

Why: this is the one prompt in the design. Undo would mean restoring a mapping you may have spent time on, so asking once is the cheaper option. The text says what happens to the mail, because "delete folder" usually sounds like "delete the mail in it".

## W18 · Selecting several conversations (`x`)

```
 homer · gmail · Mailbox                                    3 selected · 3 unread · synced 2m ago
──────────────────────────────────────────────┬───────────────────────────────────────────────
 ■ ● GitHub (4)       PR #2 merged      13:40 │ 3 conversations selected
 ■   Amazon         + Your order          Sun │
>■   Library          Books due Friday  3 Oct │ GitHub · PR #2 merged
     Hostel Office  + Room allotment    2 Oct │ Amazon · Your order
   ...                                        │ Library · Books due Friday
──────────────────────────────────────────────┴───────────────────────────────────────────────

 x select  e archive  d delete  m mark  M move  Esc clear selection
```

- `x` toggles the row and moves down, so `xxx` selects three rows in a row.
- Any action applies to all the selected rows, and one `u` undoes all of it.

## W19 · Status line states

| State | Example |
|---|---|
| Result + undo | `Archived "Leave form" · u undo` |
| Several | `Deleted 3 conversations · u undo` |
| Sending | see W10 |
| Server refused, change reversed | `! Couldn't archive "Leave form": connection lost. Put back.` (red) |
| Sign-in needed | `! gmail: sign in again with homer login -a gmail` (red, also in the header) |
| Offline | `! offline. Showing cached mail, changes are not saved` (red) |
| New mail | `2 new in Mailbox` |

`u` undoes the last action only. The undo offer stays on the line until your next action.

## W20 · Help (`?`)

```
        ┌ Keys · list ────────────────────────────────────────────────────────────┐
        │ Move           j k  ↑ ↓   g G top/bottom   space b scroll preview       │
        │ Open           Enter full view                                          │
        │ Write          c new   r reply   R reply all                            │
        │ Handle         e archive   d delete   m read/unread   M move   u undo   │
        │ Select         x select row   Esc clear                                 │
        │ Find           / search   Tab inside search: this folder or all         │
        │ Go             f folders and accounts                                   │
        │ View           T conversations or single mails                          │
        │ Quit           q                                                        │
        │                                                                         │
        │ Esc or ? close                                                          │
        └─────────────────────────────────────────────────────────────────────────┘
```

Help shows the keys of the window it was opened from (list, conversation, compose, folder pane). It's a box over the window, so you can see what the keys apply to.

## W21 · Narrow terminal (under 100 columns)

```
 homer · gmail · Mailbox            3 unread
────────────────────────────────────────────
>● Kavitha Das (2)  Trip schedule        2m
 ● Dean Academics + Leave form        14:02
 ● GitHub (4)       PR #2 merged      13:40
   To Amit          Tickets for Sunday  Mon
   ...
────────────────────────────────────────────

 Enter open  c new  r reply  e archive  f folders  ? help
```

- No preview pane. `Enter` opens W5 full screen, and `f` opens the folder pane full screen.
- Compose (W7) and the boxes (W6, W8, W14 to W17, W20) are the same, only narrower. Fields wrap instead of being cut off.
- Resizing the terminal switches between the two layouts live, keeping the cursor on the same conversation.
