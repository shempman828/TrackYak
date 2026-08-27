# TrackYak User Guide

TrackYak turns a folder of audio files into a music library you can actually
work with: browse and search everything, fix up messy metadata, build smart
playlists, track down duplicates, map out which artists influenced which,
and sync curated sets to a phone or a folder. This guide walks through each
part of the app by what you're trying to do, not just what's on screen.

## Table of Contents

**Navigation Views**
[Tracks](#tracks) · [Now Playing](#now-playing) · [Albums](#albums) ·
[Artists](#artists) · [Playlists](#playlists) · [Genres](#genres) ·
[Places](#places) · [Publishers](#publishers) · [Roles](#roles) ·
[Moods](#moods) · [Influences](#influences) · [Awards](#awards) ·
[Charts](#charts) · [Sync](#sync) · [Timeline](#timeline)

**Docks**
[Navigation Dock](#navigation-dock) · [Player Dock](#player-dock) ·
[Queue Dock](#queue-dock)

**Menu Bar**
[File Menu](#file-menu) · [Audio Menu](#audio-menu) ·
[Tools Menu](#tools-menu) · [View Menu](#view-menu) ·
[Help Menu](#help-menu)

---

## Finding Your Way Around

The main window has four parts: a **navigation list** on the left to jump
between views, the **current view** filling the middle, **transport
controls** pinned along the bottom so playback is always reachable, and a
**queue** you can pull out on the right when you want to see what's coming up
next. Click the logo at the top of the navigation list to collapse it down to
icons if you want more room.

Most views follow the same rhythm: a search or filter bar up top, a list or
grid of results, and a right-click menu on any item for what you can do with
it. Once you've got that pattern down, most of the app is discoverable by
right-clicking things.

---

# Navigation Views

## Tracks

This is the whole library, flattened into one table — the place to start when
you know roughly what you're looking for but not which view it lives in.

**Finding something**: start typing in the search bar. It matches against
every column at once (title, artist, album, and the rest), not just the
title, so searching "Buckley" turns up tracks where he's the artist as well
as any where he's mentioned in the album name. The table only loads what's on
screen at first and fetches more as you scroll, so this stays fast even in a
huge library.

**Getting the columns you want**: by default a lot of technical columns
(bit rate, format, etc.) are hidden to keep the table readable. Use the
column-visibility control in the toolbar to bring back whichever ones you
care about — they're grouped by category so you can turn a whole group on or
off at once.

**Playing something**: double-click a row to play it immediately. To queue
up a batch instead, select several tracks (Ctrl/Shift-click) and drag them
onto a playlist, or use the context menu.

**Fixing up metadata**: select one track and choose Edit to change any
field. Select several and edit them together — only fields safe to apply to
a whole batch (like genre) show up in the multi-edit form, so you won't
accidentally overwrite something track-specific like the title.

**Cleaning out tracks**: the Delete key (or the context menu) gives you a
choice — remove just the database entry and leave the file alone, or delete
the file too. The second option asks you to confirm twice, since it's
permanent.

*Quick reference: `Delete` removes selected tracks · `Ctrl+C` copies selected
rows · `Ctrl+A` selects everything · arrow keys move between rows.*

## Now Playing

The big, art-forward view of whatever's currently playing — pull this up when
you want to actually look at what you're listening to rather than manage
your library.

Album art fills the left side with a soft blurred version of it behind the
whole view. On the right, the title, artist, and album sit above a row of
quick-glance details (BPM, key, release year, play count, genres) that only
show up if the track actually has that data.

Below that, two tabs: **Lyrics** shows synced, karaoke-style lyrics if the
track has them — the current line highlights as it plays. If the timing
feels off, click the small ⏱ icon to reveal a sync-offset slider and nudge it
until it lines up. **Credits** shows everyone credited on the track.

Press `Ctrl+Shift+F` to drop into **cinema mode**, which hides the menu bar
and every dock so this view fills the whole window — press it again to bring
everything back exactly how it was.

## Albums

A cover-art grid for browsing your collection visually rather than as rows in
a table.

**Browsing**: scroll to load more as you go, drag the size slider in the
toolbar to make the covers bigger or smaller, and use the sort dropdown to
reorder by title, artist, year, rating, play count, track count, duration, or
even art resolution — or just shuffle. The search bar matches title, artist,
and year.

**Narrowing down what you see**: open the filter row for more specific
digging — a year range, a minimum track count, whether metadata review is
done, or whether an album is missing artwork. Handy for hunting down albums
that still need attention rather than ones you're trying to listen to.

**Editing an album**: click any cover to open its editor. It's organized
into tabs — Overview (with a "Look Up on MusicBrainz" button if you want to
pull in canonical data), Details, Tracks, Artwork, Aliases, Genres, Track
Credits, Album Credit, Publishers & Places, Awards, and Advanced. You don't
need to touch most of these day-to-day — Overview and Tracks cover the common
cases.

**Other things you can do**: right-click a cover (or empty grid space) for
New Album, Add to Queue, Merge into another album, Delete, Delete Empty
Albums, and Find Duplicate Albums.

## Artists

A browsable directory of every artist and group in your library, with a
Wikipedia-style writeup for each one.

**Browsing**: the list on the left can be narrowed to individuals only or
groups only, searched by name, sorted several ways, and filtered by metadata
review status, whether they have a profile image, or artist type. Groups get
a 👥 marker; artists linked to MusicBrainz get a 🔗.

**Reading about an artist**: click one and the right panel builds a
biography-style page — an infobox with the key facts up top, then whichever
sections actually apply (Discography, Membership, Credits, Awards,
Influences). A jump-to bar at the top lets you skip straight to any section
that's present.

**Managing an artist**: right-click for the full set of actions — Edit,
Merge with another artist, Split into multiple artists, add them to a group
or add a member if they're a group, credit them with an Award or a Place,
convert between individual and group, jump to their Wikipedia page, edit
their influences, or add a profile picture. There's also always-available
Add Artist, Add Group, Find Duplicate Artists, and Delete Unused Artists at
the bottom of the menu for library upkeep.

## Playlists

Both hand-picked playlists and smart playlists that build themselves from
rules, organized in one tree (nest them however makes sense to you — up to 8
levels deep).

**Making a regular playlist**: New Playlist, give it a name and description,
and start dragging tracks into it from the Tracks view or anywhere else.

**Making a smart playlist**: New Smart Playlist opens a rule builder instead.
Pick whether tracks need to match *all* your rules or *any* of them, then add
criteria rows — e.g. "Genre contains Jazz" and "Rating is greater than 4."
The value control adapts to whatever field you pick, so a date field gives
you a date picker, a rating field gives you a number range, and so on. Once
saved, the playlist keeps itself up to date — use Refresh Playlist any time
you want to force a recheck. You can't drag tracks into a smart playlist by
hand; its membership is always computed from the rules.

**Organizing your playlists**: drag one onto another to nest it underneath,
or onto empty space to pull it back to the top level. Toggle Flat View if you
just want an alphabetical list instead of the hierarchy.

**Getting tracks out**: right-click a playlist for Open Track Editor (or
View Tracks for a smart one) to see everything in it, or Export to write it
out as a standard `.m3u` file you can hand to another player.

## Genres

The genre hierarchy that tags get organized under (think Rock as a parent of
Alternative Rock, which is a parent of Grunge) — and a shortcut to every
track carrying any given genre.

**Browsing**: search narrows the tree live. Each genre shows how many tracks
carry it directly, plus a second number for tracks pulled in from its
subgenres if it has any. Right-click → View Tracks (with a Recursive toggle
to include those subgenre tracks too, or not).

**Building out the hierarchy**: New Genre to add one from scratch, or
right-click an existing genre for New Parent Genre / New Child Genre to slot
one in above or below it. Drag a genre onto another to reparent it, or onto
empty space to make it top-level. Rename any genre by clicking directly into
its name in the tree.

**Cleaning up duplicates**: if you've ended up with, say, both "Hip Hop" and
"Hip-Hop," right-click one and choose Merge to fold it into the other. Split
does the opposite — it clones a genre's full set of track relationships onto
two or more new names, useful for untangling a genre that was really
covering several distinct ones (the original is left in place, not moved).
Deleting a genre promotes its children to top-level rather than deleting them
too, and offers to add the name to your Excluded Genres list so future
auto-tagging skips it.

**Exporting**: right-click anywhere in the tree (an item or empty space) and
choose Export Hierarchy... to save the whole tree as a plain-text or
Markdown file, laid out with box-drawing connectors (`├──`, `└──`) like the
Unix `tree` command. The export always covers every genre — Flat View and
any active search filter only change what's on screen, not what gets
exported — ordered however the tree is currently sorted (by name or by track
count).

## Places

Where in the world your music comes from — venues, cities, countries — shown
as both a map and a list, and linkable to tracks, albums, and artists.

**Exploring the map**: places with coordinates show up as color-coded,
clustered markers (green for countries, blue for cities, and so on down to
custom types, which get their own stable color). Click a marker for its
details and a "View Associations" button to see everything linked to it. Use
the filter panel to show only certain place types, and the marker-stacking
control if clusters are too aggressive or not aggressive enough for your
zoom level.

**Working the list instead**: the list view organizes places in the same
parent/child hierarchy as the map (a City under its State under its
Country, for instance), with search and filters for things like "missing
coordinates" so you can find entries that still need cleanup. Drag a place
onto another to reparent it.

**Adding or fixing a place**: Add Place opens a form with a "Search
Coordinates" button that geocodes an address or place name for you, so you
usually don't need to look up latitude/longitude by hand.

**Other actions**: right-click for View Associations, View Details, Edit,
Merge (fold a duplicate into its canonical entry), New Parent/Child Place,
or Delete.

## Publishers

The record-label side of your library — who released what, and how labels
and their imprints relate to each other.

**Browsing**: search and filter by MusicBrainz-link status or metadata
review tier in the tree on the left; click any publisher to see its logo,
info, and associated places on the right.

**Editing a publisher**: fields cover description, who founded it, its
parent label, headquarters, active years, and a logo you can upload. If
you're editing an existing entry rather than creating one, you also get an
Aliases tab for alternate names.

**Untangling duplicates**: right-click → Find Duplicate Publishers to run a
fuzzy-match scan across your whole publisher list, then review and bulk-merge
whatever it finds similar. For a one-off, Merge and Split work the same way
they do for genres — Merge combines two into one, Split clones relationships
out to new names.

## Roles

The credit vocabulary used when crediting an artist on a track or album —
Guitar, Electric Guitar, Producer, and so on — organized the same
hierarchical way as Genres.

Search and sort (alphabetically, or by how often a role is actually used) in
the tree on the left; select one to see its details on the right. The status
bar at the bottom gives you a running total of how many roles are mixed
track/album use, track-only, album-only, or not assigned to anything yet —
useful for spotting roles nobody's using.

Each role shows how many album and track credits it carries directly, plus a
second number for credits pulled in from its sub-roles if it has any — same
as the Genres and Moods trees.

Rename by clicking directly into a row, or open the full Edit dialog for a
description too. Merge and Split work like they do everywhere else in the
app; New Parent Role / New Child Role let you build out sub-categories (e.g.
splitting a generic "Guitar" into "Electric Guitar" and "Acoustic Guitar").

## Moods

A tag system for how a track feels — organized as a hierarchy, same as
Genres and Roles, and searchable the same way.

Each mood shows how many tracks carry it directly, plus a second number for
tracks pulled in from its sub-moods if it has any — same as the Genres tree.
Click into an existing mood's Edit dialog and you get an "Associated Tracks"
tab for browsing (and removing tracks from) that mood directly, with a
Recursive toggle to also pull in tracks tagged with any of its child moods.
New Mood, New Parent/Child
Mood, Merge, and Delete round out the rest — see the [Genres](#genres)
section above for how those work, since the pattern is identical.

Most moods actually get applied automatically — see
[Mood Tagging](#tools-menu) in the Tools menu, which scans lyrics for you.
The same scan also runs per-track any time lyrics are searched or saved for
one — from the track edit dialog's Lyrics tab or the [Player Dock](#player-dock)'s
right-click menu — with a status message naming whichever mood(s) matched.

## Influences

A graph of who influenced whom, drawn as a force-directed network you can pan
and zoom around.

**Reading the graph**: bigger nodes influenced more other artists; nodes are
colored by cluster, using automatic community detection that groups related
artists together. The legend panel (toggle it with "Show Cluster Legend")
lets you switch between looser and tighter clustering, and you can rename a
cluster to something meaningful via its Rename button. Hover any node to see
its full name, even when the label on the graph itself is abbreviated to
fit.

**Adding a relationship**: click Add Influence and type both artist names —
it tells you as you type whether it found an existing match or will create a
new artist, so you don't end up with accidental duplicates. Remove Influence
works the same way in reverse: pick an existing relationship from a
searchable list and delete it.

**Getting unstuck**: if the layout looks tangled, Refresh Graph recomputes
it from scratch; Fit to View reframes everything to the window if you've
scrolled or zoomed away from the action.

## Awards

Tracks which artists, albums, or other entities won or were nominated for
what, organized under a hierarchy of awards and categories (a specific
Grammy category nested under "Grammy Awards," for instance).

**Browsing**: search plus Year and Category filters narrow the tree; click
an award to open its details on the right.

**Adding an award**: New Award needs just a name — category, year, and a
parent award are optional, for slotting it into an existing hierarchy.

**Crediting someone**: from an award's detail tab, use Award Relationships
to assign artists, albums, tracks, publishers, or places to it as Recipient,
Nominee, Presenter, Judge, Host, or Sponsor/Organizer.

**Editing**: the detail tab also lets you change Name, Year, Category,
Description, and Parent Award directly, and re-slot an award under a
different parent without redoing it from scratch.

Deleting an award promotes any child awards/categories up a level rather
than deleting them along with it.

## Charts

Pulls in historical Billboard-style chart data and matches it against your
library, so you can see what you have, what charted but you're missing, and
what's worth filling in.

**Getting chart data**: Download Chart Data (or Fetch Updates, once you've
already got some) pulls it in; Match Now then runs the matching pass against
your library. Both run in the background so you can keep working.

**Browsing what charted**: the Week Browser tab lets you flip through a
specific chart, year, month, and week; the Search tab does a full-text search
across every chart at once if you're looking for something specific.

**Finding what you're missing**: the Recommendations tab is the useful one —
**Missing Popular** ranks unmatched chart entries by how well they performed,
and **Gap Fills** specifically surfaces songs that would connect two runs of
chart weeks you already own, which is a good way to round out a near-complete
run without chasing everything at once.

**Matching by hand**: right-click any entry (in Week Browser, Search, or
Recommendations) for Match to Track/Album, which opens a search-and-pick
dialog. On the Recommendations tab, matching one entry automatically resolves
every week that same song appeared as unmatched, not just the row you
clicked.

Once you're happy with your matches, Generate/Update Charts Playlists builds
playlists straight from the chart data.

## Sync

Push curated playlists and moods out to an Android device or a plain folder
(for a USB drive, an old MP3 player, whatever you point it at).

**Setting up a profile**: click + New, give it a name, then head to the
Settings tab to point it at a destination — either Link Device for something
connected over USB (click ⟳ Detect first if it's not showing up), or Browse
for a folder. There's also an option to wipe the destination clean before
each sync, if you want it to exactly mirror your selection rather than
accumulate old files.

**Choosing what goes**: the Playlists & Moods tab is a checklist — tick
whichever playlists and moods you want on this device. The estimated track
count and size update live as you check things off.

**Syncing**: once a profile has both a selection and a destination, Start
Sync becomes available. It'll confirm the destination and track count before
it starts, then switch you to the Log tab to watch progress — worth checking
afterward if anything got skipped or retried.

## Timeline

Everything in your library that has a date attached — album releases, when a
track was recorded or composed, artist and publisher active years, award
years — laid out on a calendar you can browse by decade.

**Browsing**: the scrubber starts zoomed out to decades; click one to expand
into its individual years, then click a year to jump the calendar there.
Days with something on them are highlighted, with small chips showing what
kind of event it is — click a day to see the full list. If a single event
type is what you're after, use the filter dropdown to show only that type.

**A fun one**: click On This Day to see everything that ever happened on
today's date across every year in your library at once — a quick way to
notice coincidences, like two albums that happen to share a release day a
decade apart.

---

# Docks

## Navigation Dock

The list on the left for jumping between views — click any entry to switch
what's showing in the middle of the window.

Click the logo at the top to collapse it down to an icon-only strip when you
want more screen space, and click it again to bring the labels back. It also
collapses itself automatically on narrower windows and re-expands once
there's room again.

## Player Dock

The transport bar along the bottom, always present so you're never far from
play/pause no matter what view you're in.

It shows the standard playback controls (previous, play/pause, stop, next),
a seek bar you can drag to jump around in the current track, volume, a
star rating for whatever's playing, and a repeat toggle that cycles through
off, repeat-one, and repeat-all. Right-click it for quick access to editing
the current track/album/artist, searching for lyrics, or adding the track to
a playlist or mood without leaving whatever view you're in. A found lyrics
search is saved automatically and scanned for [moods](#moods) on the spot,
with the status bar naming whatever matched.

*Quick reference: `Space` play/pause · `Ctrl+.` stop · `Ctrl+→`/`Ctrl+←`
next/previous · `Ctrl+↑`/`Ctrl+↓` volume · `Shift+→`/`Shift+←` seek ·
`Ctrl+Shift+↑`/`Ctrl+Shift+↓` rate up/down half a star. Media keys on your
keyboard work even when the app isn't focused.*

## Queue Dock

What's coming up next — hidden by default since not everyone wants it taking
up space, but pull it out any time with `Shift+Q` or from the View menu.

The currently-playing track is pinned at the top; everything below it is
what'll play next, in order. Drag rows to reorder them, right-click one for
Play Next (jump it to right after the current track without losing the rest
of the queue's order), or use the Shuffle dropdown for a quick reshuffle —
including a weighted option that leans toward your higher-rated tracks.
Double-click any queued track to jump to it immediately.

---

# Menu Bar

## File Menu

- **Import Directory** — the way new music gets into your library. Add
  folders to a tracked list, check the ones you want included, and Start
  Import; it keeps running in the background even if you close the dialog,
  so you can keep working while a big import churns through.
- **Manage Library** — for when your files on disk have drifted from what's
  in the database. Analyze & Organize proposes a cleanup (renaming/moving
  files into a consistent `Artist/Album/Track` structure) that you approve
  before anything actually moves; Update Metadata pushes your database edits
  back into the actual file tags, for when you want the files themselves to
  match what you've cleaned up in TrackYak.
- **View Library Statistics** — a dashboard of your whole collection at a
  glance: health, top artists/albums/genres, audio characteristics, and more,
  split across tabs.
- **Find Duplicate Tracks** — run this after a big import if you suspect
  you've picked up copies of things you already had. It can match by
  metadata or by actual audio fingerprint if you want to catch re-encodes
  that don't share filenames or tags.
- **Find Missing Tracks** — checks for library entries whose file no longer
  exists on disk (moved, renamed outside the app, deleted) so you can clean
  up dangling entries.
- **General Settings** — display, audio device, and general app preferences,
  all in one dialog.
- **Exit** (`Ctrl+Q`) — closes the app.

## Audio Menu

- **Equalizer Settings** — adjust playback tone with a 12-band graphic EQ.
  Save your own presets or reset to flat at any point.
- **Audio File Analysis** — runs the background scan that figures out BPM,
  key, loudness, and the other audio characteristics used throughout the app
  (sorting, smart playlists, the Now Playing chips). It's safe to close this
  dialog mid-scan and reopen it later — analysis keeps running either way.

## Tools Menu

- **Manage Aliases…** — where merge/split history and alternate names live
  for Genres, Artists, Publishers, and Roles, plus a list of genre names to
  always skip during auto-tagging.
- **Recalculate Explicit Flags…** — scans lyrics for tracks that haven't had
  an Explicit flag set yet and fills it in. Never touches a flag you've set
  by hand.
- **Mood Tagging…** — the automatic side of the Moods view: scans lyrics
  against a keyword list to tag tracks with moods (and known places), and
  surfaces common lyrics words that aren't mapped to a mood yet so you can
  assign or dismiss them. For a handful of tonally-opposed mood pairs (e.g.
  Happy/Sad, Sleepy/Energetic — see `assets/mood_opposites.json`, which you
  can edit to add more), only the more strongly-matching mood of the pair
  gets tagged when both would otherwise clear the threshold; a near-even
  match still tags both rather than guessing.

## View Menu

- **Show Queue** (`Shift+Q`) — show or hide the [Queue Dock](#queue-dock).
- **Full Screen** (`F11`) — toggle full-screen mode.
- **Mini Player** (`Ctrl+M`) — pop out a small, always-on-top transport
  window, handy for keeping playback controls visible while you work in
  another app.
- **Reset Layout** — snap the navigation, queue, and player docks back to
  their default positions if you've dragged something into an awkward spot.

## Help Menu

- **About** — version and license information.
- **Support this Project** — a link to support TrackYak's development.
- **Support Wikipedia** — TrackYak leans on Wikipedia and MusicBrainz for a
  lot of its metadata lookups; this links to Wikipedia's donation page.
