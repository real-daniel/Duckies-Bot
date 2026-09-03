# Patched Deadlock live-events service

This image builds the existing `deadlock-api/deadlock-api` live-events service at the pinned
commit in `Dockerfile`, then applies `statue-events.patch`.

The patch reads permanent golden-statue modifiers from the Source 2 `ActiveModifiers` string
table and emits compact `statue_buff` SSE events. Each event contains the string-table entry ID,
hero-pawn entity index, modifier serial/subclass, stat family, and statue tier. The bot associates
the pawn with its player controller and deduplicates events by entry ID, serial, and subclass.
The parser tracks modifier identity rather than string-table slot identity because Source 2 reuses
and updates `ActiveModifiers` slots throughout a match.

The 18 modifier-subclass values were validated against Valve's post-match `power_up_buffs`
metadata across three replay matches (36 players, 913 permanent buffs, zero count differences).
They are game-build-specific and should be revalidated after a Deadlock update if counts stop
appearing.
