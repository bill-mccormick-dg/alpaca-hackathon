Output ../build/shots/shot1.raw.mp4
Set Shell "bash"
Set FontSize 22
Set Width 1920
Set Height 1080
Set Padding 30
Set Margin 0
Set Theme "Catppuccin Mocha"
Set WindowBar "Colorful"
Set TypingSpeed 8ms
Set Framerate 30

# The ssh invocation is scaffolding, not content: demo.sh prints its own caption
# and its own '$ command' line, and those are what should be on screen.
#
# VHS gets the command as a backtick string, because it contains both the ssh
# single quotes and the pacer's double quotes. The backticks are escaped: this
# heredoc is unquoted (it has to interpolate $HOST and $remote), so bare ones
# would run the whole ssh line locally instead of writing it to the tape.
Hide
Type `clear && ssh -o BatchMode=yes root@192.168.212.10 'cd /opt/alpaca-hackathon && PYTHONUNBUFFERED=1 PAUSE=0 ACCT=official CFG=config.yaml FORCE=--force SKIP_HALT=1 bash submission/video/demo.sh 1 | while IFS= read -r L; do printf "%s\n" "$L"; sleep 0.05; done; printf "\033[?25l"'`
Enter
Sleep 1500ms
Show
Sleep 100s
