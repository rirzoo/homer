# The command line against the demo server. Run from the repo root: demo/up.sh /tmp/maya && vhs demo/cli.tape
Output media/cli.gif
Set Shell bash
Set FontSize 17
Set Width 1300
Set Height 720
Set Padding 20
Set TypingSpeed 40ms
Hide
Type 'export HOME=/tmp/maya PATH=/tmp/maya/bin:$PATH PS1="\[\e[1;32m\]$ \[\e[0m\]" && cd && clear'
Enter
Show
Type "homer list -n 6"
Sleep 400ms
Enter
Sleep 3s
Type "homer read 11 --save ~/Downloads"
Sleep 400ms
Enter
Sleep 4s
Type 'homer reply 11 -b "Thanks Daniel. Comments on the billing section by Thursday."'
Sleep 400ms
Enter
Sleep 3s
Type "homer list -a personal --unread"
Sleep 400ms
Enter
Sleep 3s
Type "homer move 5 -a personal --to Archive"
Sleep 400ms
Enter
Sleep 4s
