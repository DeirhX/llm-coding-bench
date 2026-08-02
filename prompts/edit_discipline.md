When you replace text in a file, copy the text being replaced from your most recent read of
that file, never from another file that happens to contain something similar: the same
function can sit in two files, and an edit aimed at the wrong one is accepted without
complaint. Each line of a read is a number, a tab, and then the file's own text: everything
after the tab is real, leading spaces included, and nothing before it is, so a line whose
text starts immediately after the tab starts at column zero. Quote only the few lines needed
to be unique, never a whole function. After changing a file your earlier view of it is stale,
so read it again before editing it further -- but read back only the lines around your change,
using offset and limit, because every whole-file read stays in this conversation for the rest
of the session and a long file costs thousands of tokens each time. Locate things with a search
command rather than by reading files whole. If a replacement is rejected, never send the same
text again: quote less of it.
