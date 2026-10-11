# Known issues

Behaviour that's known and accepted for now, with what would change if it gets fixed. The TUI design lives in [TUI.md](TUI.md) and [FRAME.md](FRAME.md).

## I1 · On a reply, "Sent with homer" isn't the very last line of the mail

Logged 2026-10-11. Status: accepted for now.

Every mail from the TUI has to end with "Sent with homer" (D10 in FRAME.md). On a reply, the footer goes at the end of the text you wrote, and the quoted original goes below it (W9):

```
Thanks! See you on the 19th.

Sent with homer

> On Sun 11 Oct, Kavitha Das wrote:
> ...
```

So the footer is the last thing you wrote, but it isn't the last line of the mail. Read literally, "always ends with" would put it under the quote. It stays above because under a long quote nobody would see it.

To change it: move the footer below the quote in the reply and forward builders (`store.build_outgoing`). This affects the reply compose window (W9) and the HTML and plain-text output alike.

Forwards work the same way: the footer ends your note and the forwarded message follows it.

## I2 · Gmail conversations archived in webmail can reappear in Mailbox

Logged 2026-10-11. Status: accepted for now.

Mailbox is your inbox and your sent mail together. Archiving from homer moves every mail in the conversation, your sent copies included, out of both, so the conversation leaves Mailbox. Gmail's own archive button only takes the Inbox label off. Your sent copies keep the Sent label, so a conversation you archived in webmail, and replied to at some point, still shows in homer's Mailbox through those copies.

Workaround: archive it once from homer (`e`).

To change it: Mailbox could hide sent-only conversations whose received mails are all archived. That needs the whole thread's labels, one more server call per sync.
