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
[File Menu](#file-menu) · [Library Statistics](#library-statistics) ·
[Audio Menu](#audio-menu) · [Tools Menu](#tools-menu) ·
[View Menu](#view-menu) · [Help Menu](#help-menu)

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

### Autocomplete fields

Fields that let you attach an existing entity — artist credits, influences,
places, a publisher's parent or headquarters, a track's samples, a track's
album (in the track editor's Albums tab) — show a drop-down of matches as
you type. Each suggestion carries a dimmed line of
context on the right so same-named entries are easy to tell apart:

- **Artists** show their disambiguation (or active years, or just
  "Group"/"Person").
- **Places** show their type and country, e.g. `City · United Kingdom`.
- **Tracks** show their primary artist and album, e.g.
  `Radiohead · OK Computer`.
- **Albums** show their album artist and release year, e.g.
  `Michael Jackson · 1982`.
- **Publishers** show their parent label and founding year.

The chart "Match to Track/Album" dialog uses the same contextual
suggestions.

Picking a suggestion drops only the name into the field — the context is
just there to help you choose.

Most of these fields reuse an existing entity when you type a name that
already exists rather than making a duplicate. The track editor's Albums tab
is the exception: because reissues, split releases, and same-titled records
by different artists are all real, its album fields add a **Create new
"…"** row at the bottom of the suggestion list — pick it to make a brand-new
album with the typed name even when one by that name already exists. Typing
the name and confirming without picking that row still reuses the existing
album.

### Status bar

Long jobs — imports, analysis, device sync — show a progress message in the
status bar at the bottom of the window while they run. If a short message
(e.g. "Analysis resumed") appears during a job, the job's own message comes
back when the short one expires, and the bar hides once every job has
finished. This also applies to messages from jobs that run in the
background, such as device sync.

---

# Navigation Views

## Tracks

This is the whole library, flattened into one table — the place to start when
you know roughly what you're looking for but not which view it lives in.

**Starting order**: until you search or sort, the table lists your tracks in
a random order, picked fresh each time you start the program — so a big
library doesn't open on the same handful of tracks every day. The order
stays put for the rest of that session: clearing a search or using Refresh
Library brings back the same sequence. Click any column header to sort
normally instead.

**Finding something**: type in the search bar and press Enter. While the
search runs, the status text at the right of the toolbar says "Searching…";
when it finishes, it shows how many tracks matched. It matches against
every column at once (title, artist, album, and the rest), not just the
title, so searching "Buckley" turns up tracks where he's the artist as well
as any where he's mentioned in the album name. The table only loads what's on
screen at first and fetches more as you scroll, so this stays fast even in a
huge library.

**Getting the columns you want**: by default a lot of technical columns
(bit rate, format, etc.) are hidden to keep the table readable. Use the
column-visibility control in the toolbar to bring back whichever ones you
care about — they're grouped by category so you can turn a whole group on or
off at once. In the column dialog, **Reset to Default** brings back the
starting set of visible columns in their normal order. At least one column
must stay visible, so the dialog (and the header's right-click menu) will not
hide the last one.

**Playing something**: double-click a row to play it immediately. To queue
up a batch instead, select several tracks (Ctrl/Shift-click) and drag them
onto a playlist, or use the context menu. Dragging also works from the track
lists that open from an artist, role, or publisher. **Add to Playlist** and
**Add to Mood** on the context menu show how many tracks were added and how
many were already there.

**Fixing up metadata**: select one track and choose Edit to change any
field. Select several and edit them together — only fields safe to apply to
a whole batch (like genre) show up in the multi-edit form, so you won't
accidentally overwrite something track-specific like the title. If Save
fails (for example, an MBID that another track already uses), the editor
stays open with the error under the tabs, so your edits are not lost.

In the editor, the **Genres** and **Moods** tabs have a **Remove Selected**
button (or press Delete) for the selected entries. The **Awards** tab links
an award to the track: type an award name (a new name creates the award) and
click **Link Award**. The Category and Year columns come from the award
itself — edit them on the award's own detail panel.

**Classical tracks from a messy title**: on a single track, the editor's
Classical tab has a **Parse Title for Classical Data** button. It reads the
work, key, catalogue number (Op., BWV, K., RV, …), movement number and tempo
marking straight out of a title like *"Symphony No. 5 in C minor, Op. 67:
I. Allegro con brio"*, fills in whichever Classical fields are still blank
(it never touches ones you've already filled), ticks **Classical**, and
trims the title down to the bare movement name (*"Allegro con brio"*) — the
player and track views rebuild the full description from the structured
fields anyway. It shows a preview of every change first; nothing is written
until you click Save.

**Cleaning out tracks**: the Delete key (or the context menu) gives you a
choice — remove just the database entry and leave the file alone, or delete
the file too. The second option asks you to confirm twice, since it's
permanent. The same choice is available as a **Delete Track** button on the
track editor's Advanced tab, for when you decide mid-edit that a track has to
go — it removes the track and closes the editor.
If a search is active when you delete, the view keeps your search results
and only removes the deleted tracks from them.

*Quick reference: `Delete` removes selected tracks · `Ctrl+C` copies selected
rows · `Ctrl+A` selects everything · arrow keys move between rows.*

## Now Playing

The big, art-forward view of whatever's currently playing — pull this up when
you want to actually look at what you're listening to rather than manage
your library.

Album art fills the left side with a soft blurred version of it behind the
whole view. If the track has more than one image — front/back/liner covers,
plus photos of any non-album-artist contributors — it slowly cycles through
them; each contributor photo is captioned with that person's name and
instrument, and a caption too long for the card pans across so you can read
all of it. On the right, the title, artist, and album sit above a row of
quick-glance details (duration, BPM, key, time signature, recording year,
play count, genres) that only show up if the track actually has that data;
hover over a detail to see what it is. A title just wraps onto more
lines as it gets longer; only once it would need more than three does it
switch to slowly panning back and forth instead. For a track marked
**Classical**, the title is built from its Classical fields instead of the
plain track name — composer, work, catalogue number, and movement, e.g.
*"Ludwig van Beethoven: Symphony No. 5 in C minor Op. 67 — I. Allegro con
brio"* — while the performers stay on the artist line below. A classical
track with no work or movement filled in keeps its normal title. The artist line always
pans when it's too long to fit. The album line reads *Album · Subtitle ·
Year*: the subtitle (e.g. "Deluxe Edition") and the year appear only when the
album has them.

Below that, three tabs: **Lyrics** shows synced, karaoke-style lyrics if the
track has them — every line is shown in one column, and the current line
highlights as it plays and scrolls to the middle of the panel, with sung lines
above and upcoming lines below, each fainter the further it is from the
current one. The lyrics start at the top of the panel and stop scrolling once
the last line reaches the bottom; lyrics short enough to fit never scroll.
Lyrics with no timing show every line at once where they can: the text
shrinks to fit the panel (never below a comfortable reading size), and a
taller window shows them larger. Lyrics too long to fit scroll along with the
song by estimate: the line that far through the lyrics is kept in the middle
(for example, 60% of the way through the song centres the line 60% of the way
through the lyrics, allowing for a short intro and outro). No line is
highlighted, because the estimate is only approximate. **ALL LINES** and the
mouse wheel work the same as for synced lyrics.
Scrolling with the mouse wheel (or clicking **ALL LINES**) lets you browse
freely; click **ALL LINES** again to follow the song. If the timing feels
off, use the **−** / **+** buttons beside the ⏱ offset to nudge it until it
lines up (click the offset itself to reset it). If the lyrics have no timing
at all, or the timing has drifted too far for the offset to fix, click
**SYNC** to open the tap-to-sync tool: it
walks through the lyrics one line at a time, and pressing `Enter` each time
the next line starts stamps that line with the current playback position
(minus a small, adjustable reaction-time offset, since your keypress always
lands a beat after you actually hear the line start). `Backspace` undoes the
last tap if you miss the timing, and `Esc` (or **Cancel**) abandons the
session with no changes saved. A reminder of these keys is shown at the
bottom of the tool. Once every line has been tapped, **Save** writes the new
timing back to the track; if the save fails, a message says so and the tool
stays open with your taps kept, so you can try again. **Credits** shows
everyone credited on the track; a long list scrolls by itself like film
credits, and scrolling it with the mouse wheel moves it to that spot and
pauses for a moment before it continues. **About** collects the
description and bio text already entered elsewhere — the artist bio editor, the
album, genre, label, and mood editors — for the track, its album, its artists,
its label(s), genre(s), and mood(s), shown read-only as a scrolling stack of
cards; entities with nothing written get no card, and a track with no
descriptions anywhere just says so. Tracks flagged instrumental have no lyrics
by definition, so the **Lyrics** tab is greyed out for them and the view stays
on **Credits**.

Press `Ctrl+Shift+F` to drop into **cinema mode**, which hides the menu bar
and every dock so this view fills the whole window. Cinema mode also shows the
song's elapsed and remaining time under the album art, since the player dock
is hidden; outside cinema mode that strip is hidden. Press it again to bring
everything back exactly how it was.

Press `Ctrl+Shift++` to toggle **auto-cycle**, which rotates the tab pane
(Lyrics → Credits → About → repeat) on its own every few seconds so you can
leave the view running hands-off — pair it with cinema mode for a full-screen
display. Disabled tabs (e.g. Lyrics on an instrumental track) are skipped, and
pressing it again stops the rotation on whatever tab is showing.

## Albums

A cover-art grid for browsing your collection visually rather than as rows in
a table.

**Browsing**: scroll to load more as you go, drag the size slider in the
toolbar to make the covers bigger or smaller, and use the sort dropdown to
reorder by title, artist, year, rating, play count, track count, duration, or
even art resolution — or just shuffle. The search bar matches title, artist,
and year. With **developer mode** on (see General Settings), the sort dropdown
also gains a "Primary Artist Count" option, which orders albums by how many
distinct primary artists are credited across their tracks; the new option
appears after a restart.

**Narrowing down what you see**: open the filter row for more specific
digging — a year range, a minimum track count, whether metadata review is
done, or whether an album is missing artwork. The **Type** and **Media**
drop-downs narrow to a single release type (Album, EP, Live, Compilation…)
or physical/digital format (CD, Vinyl, Cassette, Digital Media…); each lists
only the values that actually occur in your library. Handy for hunting down
albums that still need attention rather than ones you're trying to listen
to. The sort choice, search text, and filter-row settings are all remembered
and restored the next time you open the app.

**Editing an album**: click any cover to open its editor. It's organized
into tabs — Overview (with a "Look Up on MusicBrainz" button if you want to
pull in canonical data), Details, Tracks, Artwork, Aliases, Genres, Track
Credits, Album Credit, Publishers & Places, Awards, and Advanced. You don't
need to touch most of these day-to-day — Overview and Tracks cover the common
cases.

The Details tab carries the per-pressing metadata: language, release type,
catalog number, release country, and **Media Format** — the physical or
digital carrier (CD, 12" Vinyl, Digital Media, Cassette, and so on; mixed
multi-disc sets read as e.g. "CD/DVD-Video"). A MusicBrainz lookup fills
Media Format in from the matched release when the field is still blank, and
it has a completer offering the common carriers plus whatever's already used
elsewhere in your library.

When you pick a release, the "importing details" step shows what it's
fetching (track relationships, writing credits, record labels, location
hierarchy) rather than an unlabelled spinner. If MusicBrainz is slow or
flaky, individual lookups are retried once and then skipped rather than
stalling the whole import — you'll get everything that came back plus a note
listing what was missed, and re-running the lookup later picks up the gaps.

The Genres and Track Credits tabs show what's common to *every* track on the
album, and editing them there applies the change to all of the album's
tracks. The Publishers & Places tab works the same way for places: below the
album-level place associations, a **Track Places** section lists the places
shared by every track, and adding or removing one there writes it to all of
them.

On the Artwork tab, choosing or clearing a cover embeds the image into every
track file on the album. That now happens in the background — the cover
buttons and Save/Cancel grey out with a wait cursor and an "Embedding
artwork…" note while it runs, then the preview updates — so a big image or a
long tracklist no longer freezes the editor while it works.

**Other things you can do**: right-click a cover (or empty grid space) for
New Album, Add to Queue, Merge into another album, Delete, Delete Empty
Albums, and Find Duplicate Albums. In the Find Duplicate Albums results, you
can dismiss a pair that is not a true duplicate. YakTrack will not show that
pair again.

## Artists

A browsable directory of every artist and group in your library, with a
Wikipedia-style writeup for each one.

**Browsing**: the list on the left can be narrowed to individuals only or
groups only, searched by name, sorted several ways, and filtered by metadata
review status, whether they have a profile image, artist type, whether they
are linked to MusicBrainz, or whether they have any tracks in your library.
Groups get a 👥 marker; artists linked to MusicBrainz get a 🔗.

**Reading about an artist**: click one and the right panel builds a
biography-style page — an infobox with the key facts up top, then whichever
sections actually apply (Discography, Membership, Credits, Awards,
Influences). A jump-to bar at the top lets you skip straight to any section
that's present. **Credits** breaks down every role the artist is credited
in, each as its own table of the tracks and albums involved; the tables
start collapsed, and each role heading can be expanded with its toggle.

**Managing an artist**: right-click for the full set of actions — Edit
(the Basic tab includes a **Sort Name** field — the filing form of the
name, like "Beatles, The" or "Davis, Miles"; it's filled automatically
from MusicBrainz on import and can be edited by hand, and it now drives
every alphabetical artist ordering — the artist list's Name sort, the
artist pickers, and the Album and Track views' sort-by-artist — so "The
Beatles" files under B; where it's blank the plain name is used instead),
Merge with another artist, Split into multiple artists, add them to a group
or add a member if they're a group, credit them with an Award or a Place,
convert between individual and group, jump to their Wikipedia page, edit
their influences, or add a profile picture. There's also always-available
Add Artist, Add Group, Find Duplicate Artists, and Delete Unused Artists at
the bottom of the menu for library upkeep. In the Find Duplicate Artists
results, you can dismiss a pair that is not a true duplicate. YakTrack will
not show that pair again. If a checked pair has fields that actually differ
(biography, MBID, links, and so on), merging opens a Resolve Conflicts step
first, letting you pick which value survives field by field before that pair
is merged; a pair with no differing fields merges straight away. Reviewing a
big batch? Skip All Remaining keeps the canonical entry's fields for every
pair still waiting, without opening a step for each one. Deleting an artist
also deletes its stored profile picture from disk, merging two artists keeps
only the surviving picture (renaming its file to match), and clearing or
replacing a picture removes the file it used to point at.

**Tags**: separate from Type, the artist editor has its own **Tags** tab for
free-form labels you define yourself, for whatever criteria matter to you —
mood, era, backstory, anything. Unlike Type, tags are grouped into
categories you create (Tag Types), and each category can have its own
hierarchy (say, a "Religion" Tag Type with Christianity as a parent of
Catholicism). YakTrack ships with no Tag Types built in — Manage Types…
adds, renames, or deletes one, and its Move Up/Down buttons set the
category order every artist's Tags tab lays its sections out in. The Tags
tab shows every category as its own section in that order; tags already in
a category that this artist doesn't have yet appear as "+ name" suggestion
pills — click one to tag the artist with it instantly, no typing needed. To
add a tag that doesn't exist yet, type a name into a section's search box
and press Add (or Enter); it's created on the fly (or reused if a tag by
that name already exists in that category). Every assigned tag shows as a
removable "name ×" chip — click it to untag the artist immediately, the
same one-click removal as before. Each section's Manage… renames, describes,
reparents by drag-and-drop, or deletes tags within that one Tag Type —
deleting a Tag Type deletes every tag under it, and every artist's
assignment to those tags, along with it.

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
you a date picker, a number field gives you a number box ("between" gives you
two: a low and a high value), and so on. For Genre Names and Place Names, type
several values separated by commas. You can't remove the last criteria row,
so its delete button is greyed out. Once
saved, the playlist keeps itself up to date — use Refresh Playlist any time
you want to force a recheck, or tick "Refresh automatically when the app
starts" so it re-checks itself every time you open the Playlists view. You
can't drag tracks into a smart playlist by hand; its membership is always
computed from the rules.

**Organizing your playlists**: drag one onto another to nest it underneath,
drop it between two rows to place it next to them at that level, or onto
empty space to pull it back to the top level. Select several playlists
(Ctrl/Shift-click) to move them all in one drag. A move that would nest a
playlist more than 8 levels deep is refused with a message. Toggle Flat View
if you just want an alphabetical list instead of the hierarchy. Double-click
(or press F2 on) a playlist's name to rename it in place.

**Deleting playlists**: select one or more playlists and press Delete, or
right-click and choose Delete. One confirmation covers the whole selection.
Sub-playlists that you did not select move to the top level. An open track
window for a deleted playlist closes.

**Getting tracks out**: right-click a playlist for Open Track Editor (or
View Tracks for a smart one) to see everything in it, or Export to write it
out as a standard `.m3u` file you can hand to another player. The file keeps
the playlist's track order. Tracks stored outside the playlist folder are
written with their full path, so other players can find them. Characters that
are not allowed in file names (such as `/` or `?`) become `_` in the file name.
Track counts in the tree update as soon as you add or remove tracks in a track
window, and an open track window reloads after its smart playlist refreshes.

## Genres

The genre hierarchy that tags get organized under (think Rock as a parent of
Alternative Rock, which is a parent of Grunge) — and a shortcut to every
track carrying any given genre.

**Browsing**: search narrows the tree live. Each genre shows how many tracks
carry it directly, plus a second number for tracks pulled in from its
subgenres if it has any — the "Tracks (Own · Recursive)" column header names
which number is which. Right-click → View Tracks (with a Recursive toggle
to include those subgenre tracks too, or not).

**Building out the hierarchy**: New Genre to add one from scratch — pick a
parent right there in the dialog, or leave it top-level — or right-click an
existing genre for New Parent Genre / New Child Genre to slot one in above or
below it. Drag a genre onto another to reparent it, or onto empty space to
make it top-level. Rename any genre by clicking directly into its name in the
tree. The Parent Genre field in these dialogs is a button, not a plain list —
click it to browse the hierarchy through nested submenus, or type to search
and pick a match.

**Cleaning up duplicates**: genre names are case-insensitive, so "Rock" and
"rock" count as the same name — creating or renaming a genre to a name that
already exists (in any case) is rejected. If you've ended up with, say, both
"Hip Hop" and "Hip-Hop," right-click one and choose Merge to fold it into the
other. Split does the opposite — it clones a genre's full set of track
relationships onto two or more new names, useful for untangling a genre that
was really covering several distinct ones (the original is left in place,
not moved). Deleting a genre promotes its children to top-level rather than
deleting them too, and offers to add the name to your Excluded Genres list so
future auto-tagging skips it.

**Exporting**: right-click anywhere in the tree (an item or empty space) and
choose Export Hierarchy... to save the whole tree as a plain-text or
Markdown file, laid out with box-drawing connectors (`├──`, `└──`) like the
Unix `tree` command. The export always covers every genre — Flat View and
any active search filter only change what's on screen, not what gets
exported — ordered however the tree is currently sorted (by name or by track
count).

## Places

Where in the world your music comes from — venues, cities, countries — shown
on a **Map** tab and a **List** tab, and linkable to tracks, albums, and
artists. The switch at the top left changes tabs, and the page remembers the
tab you used last. **+ Add Place** at the top right works from either tab.
Each tab has its own type filter.

**Exploring the map**: places with coordinates show up as color-coded,
clustered markers (green for countries, blue for cities, and so on down to
custom types, which get their own stable color). The legend in the bottom
left corner shows which color is which type. Click a marker for its details,
a "Connected Music" button to see everything linked to it, and a "Show in
List" button that opens the place in the List tab. When the map fits to the
markers, it does not zoom in closer than city level, so a single marker still
shows its surroundings. Places with coordinates that are not valid (for
example, a latitude above 90) are not put on the map; the "N places not on
map" link includes them. Use the **Type** button to
show only certain place types, and **Stacking** if clusters are too
aggressive or not aggressive enough for your zoom level. The "N places not
on map" link opens the List tab filtered to the places that still need
coordinates.

**Working the list instead**: the list shows places in their parent/child
hierarchy (a City under its State under its Country, for instance), or as one
flat list with **Tree / Flat**. Sort with **A-Z / Most used**. Each row shows
the type's color, the type, a count of the music connected to the place and
the places inside it, and a ⚠ mark when the place has no coordinates or no
type. The filter chips under the search box are always visible: **No
coordinates** and **No MBID** show how many places need that repair, and
**No parent** shows only top-level places. Clear Filters resets the search
box, type filter, and all chips in one click. Drag a place onto another to
reparent it. You cannot drop a place onto itself or onto one of its own child
places. A place whose parent data loops back to itself shows at the top level
so that you can find it and set a correct parent. Use Expand all / Collapse all to open or close every branch at
once (disabled in the Flat list, where there's nothing to expand).

**The detail panel**: select a place and the panel on the right shows its
parent chain (click a parent to go to it), its type, coordinates, and
MusicBrainz link, its description, and the music connected to it. Switch
**Direct / With children** to include music connected to places inside it.
**Show on Map** opens the Map tab at that place.

**Adding or fixing a place**: the form has three sections. Under Location,
type an optional region and click **Find Coordinates** to geocode the name;
when the name matches more than one place, click the correct result in the
list. The search runs in the background, so the form stays usable while it
runs. The Type field shows matching types from your library as you type, but
you can still type a new type that is not in the list. Latitude must be from
-90 to 90 and longitude from -180 to 180, and you must enter both or
neither. Mistakes, such as a coordinate that is out of range, a parent that
does not exist, or a parent that is the place itself or one of its child
places, show in red under the field. If you do not change the Parent field,
the place keeps its parent, even when another place has the same name.

**Other actions**: right-click a place (or use the ⋯ button in the detail
panel) for Edit, Merge (fold a duplicate into its canonical entry), New
Parent/Child Place, or Delete. New Child Place fills in the Parent field for
you. New Parent Place fills in the place's current parent, so the new place
goes between the two. When you delete a place, its child places move up to
the deleted place's parent; they do not lose their country chain. If a save,
move, or delete fails, an error message tells you. Select more than one place and the panel
offers Edit N Places and Delete N Places. Bulk-edit changes Type, Latitude,
Longitude, Description, and Parent Place across all of them at once — a
field left blank because the selection disagreed on it is left alone, not
cleared; only fields you actually change are applied.

**Untangling duplicates**: right-click → Find Duplicate Places to run a
fuzzy-match scan across your whole place list, then review and bulk-merge
whatever it finds similar. The scan checks both the name and the
surrounding country/state/city context, so two same-named places in
different countries won't be flagged against each other. Found a pair that
is not a true duplicate? Dismiss it, and it will not show up in a later
scan. If a checked pair has fields that actually differ, merging opens a
Resolve Conflicts step first, letting you pick which value survives field by
field; a pair with no differing fields merges straight away, and Skip All
Remaining keeps the canonical entry's fields for the rest of a big batch
without reviewing each pair.

## Publishers

The record-label side of your library — who released what, and how labels
and their imprints relate to each other.

**Browsing**: search and filter by MusicBrainz-link status or metadata
review tier in the tree on the left. Click a publisher, or move to it with
the arrow keys, to see its info and associated places on the right. A 🔗
after a name means it is linked to MusicBrainz.

**Organizing the tree**: drag a publisher onto another to make it a child,
or onto empty space to make it top-level. You cannot drop a publisher onto
one of its own children. To rename, press F2, double-click the name, or
right-click → Rename Publisher. A blank name is not saved. Right-click
empty space → Add New Publisher opens the full publisher form.

**Deleting**: select one or more publishers and press Delete (or
right-click → Delete). If a deleted publisher has child publishers, they
move up one level to the deleted publisher's parent, and the confirmation
tells you how many will move.

**Editing a publisher**: fields cover description, who founded it, its
parent label, headquarters, and active years. If you're editing an existing
entry rather than creating one, you also get an Aliases tab for alternate
names. A founder name that does not match an existing artist shows as
"(new)" and is created only when you click OK. The founded year cannot be
after the defunct year, and the MBID must be a full MusicBrainz ID.

**Viewing a publisher's albums**: click View Albums to open its album list.
Double-click an album there to open it in the album editor.

**Untangling duplicates**: right-click → Find Duplicate Publishers to run a
fuzzy-match scan across your whole publisher list, then review and bulk-merge
whatever it finds similar. For a one-off, Merge and Split work the same way
they do for genres — Merge combines two into one, Split clones relationships
out to new names. Found a pair that is not a true duplicate? Dismiss it, and
it will not show up in a later scan. If a checked pair has fields that
actually differ, merging opens a Resolve Conflicts step first, letting you
pick which value survives field by field; a pair with no differing fields
merges straight away, and Skip All Remaining keeps the canonical entry's
fields for the rest of a big batch without reviewing each pair.

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
To move a role somewhere else in the tree, either drag it onto its new
parent, or right-click it and use Change Parent, which cascades into a
submenu of every other role (nested the same way the tree is) plus a
Top Level (No Parent) option at the top — the role's own sub-roles are left
out of that submenu since moving a role under one of its own children isn't
possible.

Deleting a role offers to add its name to your Excluded Roles list so future
imports skip that credit — the same parse-ignore idea as Excluded Genres.
The list itself lives in the **Skipped Roles** tab of Manage Aliases…
(Tools menu): any credit role named there is never attached to a track on
import, whether the credit came from file tags or MusicBrainz.

**Exporting**: right-click anywhere in the tree (an item or empty space) and
choose Export Hierarchy... to save the whole tree as a plain-text or
Markdown file, laid out with box-drawing connectors (`├──`, `└──`) like the
Unix `tree` command. The export always covers every role — any active search
filter only changes what's on screen, not what gets exported — ordered
however the tree is currently sorted (by name or by count).

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
section above for how those work, since the pattern is identical. Select
several moods (Ctrl/Shift-click) and the right-click menu offers a single
"Delete N Moods" that removes all of them and their track associations at
once.

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
lets you switch between looser and tighter clustering — the "Detail" buttons
are labeled by how many groups each option splits the graph into — and you
can rename a cluster to something meaningful via its Rename button. A spinner
covers the graph while it is being built or re-grouped. Hover any node to see
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

**Browsing**: search plus Year, Category, and Award Name filters narrow the
tree; click an award to open its details on the right. Selecting another
award replaces that panel — only one award's details are shown at a time.
The Award Name filter opens a popup where you can type to search the list of
names and tick one or more to show; leaving everything ticked (or nothing
ticked) shows all names.

**Adding an award**: New Award needs just a name — category, year, and a
parent award are optional, for slotting it into an existing hierarchy.

**Crediting someone**: from an award's detail panel, use Award Relationships
to assign artists, albums, tracks, publishers, or places to it as Recipient,
Nominee, Presenter, Judge, Host, or Sponsor/Organizer.

**Editing**: the detail panel also lets you change Name, Year, Category,
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
your library. Both run in the background so you can keep working. Once
matching has been running a few seconds, the status line adds an ETA showing
roughly how much longer it'll take.

Matching is deliberately strict: a chart entry is only auto-linked when the
title and the primary artist both line up as whole words (edition noise like
"(2011 Remaster)" or a trailing "Featuring ..." is allowed, but "King" won't
grab "Kingston" and "R.E.M." won't grab "Jeremy Soule"), and a track whose
known release year is *after* the week it charted is never matched (unless
its title is flagged as a remaster/reissue). Anything it isn't sure about is
left unmatched for you to link by hand.

**Browsing what charted**: the Week Browser tab lets you flip through a
specific chart, year, month, and week; the Search tab does a full-text search
across every chart at once if you're looking for something specific.

**Finding what you're missing**: the Recommendations tab is the useful one —
**Missing Popular** ranks unmatched chart entries by how well they performed,
and **Gap Fills** specifically surfaces songs that would connect two runs of
chart weeks you already own, which is a good way to round out a near-complete
run without chasing everything at once. The **Years** button scopes both
rankings to one year or a whole decade — click a decade for its submenu, then
"Entire decade" or a single year; it defaults to All Years. Both rankings
scan every chart week in range, so after you open the tab, change the chart
filter, run length, or year, or match an entry, "Computing recommendations…"
shows briefly while the list rebuilds in the background — the rest of the app
stays responsive meanwhile. Both tables start in ranked order; click a column
header to sort by it instead (Peak, Weeks on Chart, and Connects sort by
their number, not as text), and click again to reverse the order. Drag a
column border in the header to make that column wider or narrower.

**Matching by hand**: right-click any entry (in Week Browser, Search, or
Recommendations) for Match to Track/Album, which opens a search-and-pick
dialog. On the Recommendations tab, matching one entry automatically resolves
every week that same song appeared as unmatched, not just the row you
clicked.

Once you're happy with your matches, the chart-playlist button builds playlists
straight from the chart data. It's contextual: it reads Generate Charts
Playlists until a chart-derived playlist tree exists, then Update Charts
Playlists for every run after that. A matched track is only filed under a
given chart year when its own release/recorded year is within two years of it,
so a catalog re-entry or a greatest-hits match (a 60s song that charts again
today) won't land in this decade's playlist; tracks with no known year are
left out of the year/decade playlists entirely.

Re-running it keeps the playlists in step with your matches in both
directions: clearing or changing a match removes that track from the year,
decade, and chart-root playlists on the next run, and a year or decade
playlist that ends up with no matched tracks at all is emptied and then
deleted (unless you've nested your own playlist under it, which keeps that
branch).

## Sync

Push curated playlists and moods out to an Android device or a plain folder
(for a USB drive, an old MP3 player, whatever you point it at).

**Setting up a profile**: each device or folder gets its own profile, shown as
a card in the **DEVICES** sidebar. With no profiles yet, the Sync screen offers
**Detect Android device**, **Choose folder…**, or an empty profile. Later, use
**+ New** (then set a destination on the **Options** page) or **⟳ Detect**,
which offers a profile for every connected phone that doesn't have one yet.
Cards can be selected with the mouse or with Tab and Enter/Space. The header
above the pages shows the profile's name, whether its phone is connected, and
where it syncs to; **Change…** picks another device or folder, and the **⋯**
menu renames or deletes the profile (deleting a profile never deletes files).
Connection badges refresh every few seconds while the Sync screen is open.

**Destination** (Options page): switch between **Android device** and
**Folder**. For a phone, **Link device…** picks from the connected devices
(USB set to File Transfer). **Music folder** is a path inside the phone's
internal storage — `Music` by default. To use another storage, start the path
with its name as the phone shows it, for example `SD card/Music`. Empty paths
fall back to `Music`, and `..` parts are ignored. Playlists go in a
`Playlists` folder next to the music folder. For a folder profile, files go
into `music/` and the `.m3u` playlists into `playlists/` inside the chosen
folder.

**Files on the destination** (Options page) is a three-way switch:
**Keep** only adds missing tracks. **Remove untracked** (the default for new
profiles) also deletes tracks and `.m3u` files that belong to playlists or
moods you've since unticked, so the destination keeps matching your
selection. It only touches files it recognises as its own (the
`Artist - Title.ext` it writes) and `.m3u` files — anything you dropped into
the folder by hand is left alone. Profiles created before this option existed
start on **Keep**. **Wipe, then copy** empties the music and playlist folders
before every sync and copies everything again — slow, but an exact mirror.

**How synced files are named**: each copied track lands as `Artist - Title.ext`.
The "Artist" is the release's **album artist** when it has one (so a track that
credits a dozen guests still files under the headliner), otherwise the track's
own primary artists joined with `&`, otherwise `Various Artists`. Characters
that aren't safe in a filename are dropped; a name with nothing left becomes
`Unknown Artist` / `Untitled`. If a name would be too long for the destination
filesystem it's trimmed and given a short ` ~xxxxxxxx` tag so two different
long names can't collide. If two *different* tracks still end up with the same
name, only the first is copied and the second is listed as a failure — it is
never silently swapped for the other song.

**Convert lossless files to MP3** (Options page): when it's on, lossless
sources (FLAC, WAV, AIFF) are re-encoded to a constant-bitrate MP3 (pick
320 / 256 / 192 / 128 kbps) as they're copied. Already-lossy files (MP3, AAC,
M4A, OGG) are copied through untouched, and the originals in your library are
never modified — tags and embedded cover art carry over. Several files are
converted in parallel. The converted files are kept in a local `cache/` folder
so a re-sync doesn't re-encode anything; **Clear MP3 cache** empties it and
shows how much space it's using (it's unavailable while a sync is running).
**Cache limit** caps that folder: after a sync that converts to MP3, the least
recently used conversions are deleted until the cache is back under the limit.
Set it to `0` ("Unlimited") to turn this off. This option needs `ffmpeg` on
your PATH (`sudo apt install ffmpeg` on Debian/Ubuntu) — without it the
checkbox is disabled, and if `ffmpeg` goes missing at sync time the run falls
back to copying originals and says so.

**Choosing what goes** (Music page): a checklist of playlists and moods under
PLAYLISTS and MOODS headers, each header showing how many are selected. Type in
the filter box to narrow the list; **Select All** / **Select None** only affect
the rows the filter shows, and **Expand All** / **Collapse All** open or close
every folder. Ticking a parent playlist doesn't tick its sub-playlists — right-
click it to select or clear it together with everything under it. The bottom
bar shows the estimated track count and size; a track in more than one ticked
playlist or mood is counted once, the same way it only lands on the device
once. With MP3 conversion on, the size becomes a post-conversion estimate
(`~… after conversion`).

**Syncing**: once a profile has both a selection and a destination, **Sync
now →** in the bottom bar becomes available (its tooltip says what's missing
if not). It confirms the destination and track count first, including a
warning when files will be removed or the destination wiped. While it runs,
the bottom bar shows the current step and one overall progress bar, and the
**Activity** page gets a dot. Changing a profile's options during a run
doesn't affect the run in progress. **Cancel** stops once the file currently
copying finishes; anything already on the destination stays there, and the
remove step is skipped on a cancelled run. Closing the app during a sync
cancels it cleanly.

**Results** (Activity page, or **Details** in the bottom bar): a headline,
totals (copied, skipped, converted, failed, removed), and one row per playlist
or mood. Any track that couldn't be copied is listed under its playlist with
the artist, title, and the reason (source file missing, transport error,
couldn't be converted to MP3, name already used by another track, or copied
but not verified on the device). Files removed by the remove step get their
own row. If the playlist file itself couldn't be written, or the destination
couldn't be wiped, that's noted too. **Show log** reveals the plain-text log
for copying out.

## Timeline

Everything in your library that has a date attached — album releases, when a
track was recorded or composed, artist and publisher active years, award
years — laid out on a calendar you can browse by decade.

**Browsing**: the scrubber starts zoomed out to decades; click one to expand
into its individual years, then click a year to jump the calendar there.
Days with something on them are highlighted, with small chips showing what
kind of event it is — click a day to see the full list. If a single event
type is what you're after, use the filter dropdown to show only that type.

Each entry in a day's list shows the name, what happened (release, recording,
band formed, and so on), and the credited artist; album releases also show
the album artist and the cover art when it's cached.

**A fun one**: click On This Day to see everything that ever happened on
today's date across every year in your library at once — a quick way to
notice coincidences, like two albums that happen to share a release day a
decade apart.

---

# Docks

## Navigation Dock

The list on the left for jumping between views — click any entry to switch
what's showing in the middle of the window. You can also use the keyboard:
press `Tab` to move focus to the list, use the arrow keys to select an entry,
and press `Enter` or `Space` to open it.

Click the logo at the top to collapse it down to a narrow logo strip when you
want more screen space, and click it again to bring the list back
(`Ctrl+Shift+N` does the same).

Right-click the list for a "Customize Navigation…" option to drag entries
into your own order or hide the ones you don't use. **Show All** and
**Hide All** check or clear every entry at once. Tracks always stays
shown, since it's the default view.

## Player Dock

The transport bar along the bottom, always present so you're never far from
play/pause no matter what view you're in.

It shows the standard playback controls (previous, play/pause, stop, next),
a waveform seek bar showing the current track's loudness envelope that you
can click or drag to jump around, volume, a star rating for whatever's
playing, and a repeat toggle that cycles through off, repeat-one, and
repeat-all. The first time you play a track its waveform is built from the
audio and cached under `cache/` (a few KB per track), so it draws instantly
on every later play; until it's ready — or if the file can't be read — the
bar falls back to a plain progress fill and stays seekable. The cache is
capped (default 256 MB, `[player] waveform_cache_max_mb` in `config.ini`,
`0` disables the cap) and the least-recently-played entries are dropped once
it's over. Settings → Audio has a "Perceptual (Log) Waveform Display" toggle
that redraws the envelope on a log curve instead of true linear amplitude —
useful for already-loud masters that otherwise look like a solid block; the
cached peak data itself doesn't change, only how it's drawn. Right-click it
for quick access to editing
the current track/album/artist, searching for lyrics, or adding the track to
a playlist or mood without leaving whatever view you're in. A found lyrics
search is saved automatically and scanned for [moods](#moods) on the spot,
with the status bar naming whatever matched. `Ctrl+Shift+L` kicks off the
same lyrics search from the keyboard. The search looks up the track's title,
primary artist, album and length together, then retries with just title and
artist if that finds nothing. It runs in the background, so closing the
player or the track edit dialog mid-search never freezes the app; a result
that arrives after you close it is simply dropped.

*Quick reference: `Space` play/pause · `Ctrl+.` stop · `Ctrl+→`/`Ctrl+←`
next/previous · `Ctrl+↑`/`Ctrl+↓` volume · `Shift+→`/`Shift+←` seek ·
`Ctrl+Shift+↑`/`Ctrl+Shift+↓` rate up/down half a star · `Ctrl+Shift+L`
search lyrics for the current track. Media keys on your keyboard work even
when the app isn't focused, and on Linux desktops (GNOME, KDE) the system's
media widget/lock screen shows the current track's title, artist, and album,
and its repeat control stays in sync with the repeat toggle above. The Linux
media-key and media-widget support needs the optional `dbus-python` and
`PyGObject` packages; without them the app runs normally, just without it.*

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
  so you can keep working while a big import churns through. A progress bar
  tracks files processed, and the Add/Remove buttons are disabled while an
  import is running. When an import finishes (or is cancelled), if any
  album it added tracks to has tracks that disagree on their embedded cover
  art — including a disagreement with tracks that album already had — a
  reconciliation window opens listing
  those albums so you can pick which version to embed into every track. It
  covers only the albums from that import; the full-library equivalent is
  Tools → [Artwork Conflicts…](#tools-menu).
  Supported file types are MP3, FLAC, WAV, M4A, AAC, OGG and Opus. M4A and
  AAC (and, on older Linux audio libraries, Opus/OGG) are decoded through
  `ffmpeg` at playback time — if it isn't on your PATH (`sudo apt install
  ffmpeg` on Debian/Ubuntu) those tracks still import, but pressing play
  shows a message telling you to install it.
- **View Library Statistics** — a dashboard of your whole collection at a
  glance: health, top artists/albums/genres, audio characteristics, and more,
  split across tabs. See [Library Statistics](#library-statistics) below for
  what each tab holds.
- **General Settings** — display, audio device, and general app preferences,
  all in one dialog. Appearance changes (theme, UI scale, font, menu bar
  auto-hide, explicit-content options) show immediately as a preview.
  **OK** keeps them; **Cancel** (or closing the dialog) undoes every
  Appearance change made since the dialog opened or since you last clicked
  **Apply**. If saving fails, **OK** shows the error and keeps the dialog
  open. Loudness normalization controls are available only while the player
  is running. The **Developer** tab holds an "Enable developer mode"
  toggle that unlocks experimental/diagnostic options (currently the
  "Primary Artist Count" album sort, and "Write file metadata immediately on
  change" — see below); some of what it exposes needs a restart to appear.
  "Write file metadata immediately on change" is a second checkbox, only
  usable while developer mode is on: once both are checked, any edit that
  would mark a track's audio file out of date (the same trigger the **Update
  Audio File Metadata** dialog's dirty-tracking uses) writes that file
  immediately instead of waiting for a manual batch run. It writes
  synchronously, so an edit touching many tracks at once (e.g. a bulk
  multi-select edit) can briefly pause the UI while every affected file is
  rewritten.
  Settings live in `config/config.ini` and are written safely (a crash
  during a save cannot leave a half-written file). If you edit that file by
  hand and a value is not valid (e.g. `volume = loud`), the app uses that
  setting's default instead of failing to start. The UI scale is limited to
  50–300 %, and thin 1px borders stay visible at small scales. Themes load
  from the app's own `themes/` folder, whichever directory you start the app
  from.
- **Exit** (`Ctrl+Q`) — closes the app.

## Library Statistics

Opened from **File → View Library Statistics**. A read-only snapshot taken
when the dialog opens (each tab has a **Recompute** button to refresh it
without reopening). Tabs: Overview, Library Health, Artists, Albums,
Genres & Moods, Places & Credits, Audio Profile, and Lyrics.

**Genres & Moods** collects the genre- and mood-related leaderboards:

- **Genre Timeline** — pick a genre from the dropdown to see how many of
  its albums came out in each release year, plus its **Earliest** and
  **Latest Release**. An album belongs to a genre when any of its tracks
  is tagged with it, and counts once. Sub-genres are not included in their
  parent, and albums with no release year are left out. Below that,
  **Longest / Shortest Lasting Genres** rank genres by span (latest release
  year − earliest). Use the **Min 3 / Min 5 / Min 10** buttons to set how
  many albums a genre needs to be ranked (default 5).
- **Top Moods by Plays** and **Most / Least Played Mood** — mood totals by
  play count.
- **Highest / Lowest Rated Mood** — outlier-controlled average track
  rating per mood.
- **Most Representative Tracks per Mood** — pick a mood from the dropdown
  to see the 5 tracks whose lyrics match that mood's keyword list most
  strongly. "Strength" is the match density (keyword hits ÷ total lyric
  words) recorded when [Mood Tagging](#tools-menu) first tagged the
  track, shown as a percentage. Only moods the automatic tagger has
  actually matched on at least one track appear in the dropdown;
  hand-added mood tags with no keyword match don't rank.

- **Equalizer Settings** — adjust playback tone with a 12-band graphic EQ.
  Save your own presets or reset to flat at any point.
- **Audio File Analysis** — runs the background scan that figures out BPM,
  key, loudness, and the other audio characteristics used throughout the app
  (sorting, smart playlists, the Now Playing chips). It's safe to close this
  dialog mid-scan and reopen it later — analysis keeps running either way.

## Tools Menu

- **Organize Files…** — for when your files on disk have drifted from what's
  in the database. Analyze & Organize proposes a cleanup (renaming/moving
  files into a consistent `AlbumArtist/Album/Track` structure) that you
  approve before anything actually moves.
- **Write Metadata…** — pushes your database edits back into the actual file
  tags, for when you want the files themselves to match what you've cleaned
  up in TrackYak. Scan the library first (dirty tracks only by default, or a
  full rescan), pick a write mode, optionally preview, then write. Writing
  works for MP3, FLAC and Ogg Vorbis files; Opus streams (even with an
  `.ogg` extension) are skipped rather than risk damaging them. "Replace all
  tags" clears every other tag but always keeps embedded cover art. Cancel
  stops after the file currently being written and reports how many files
  were updated; the rest stay flagged, so the next scan finds them again.
  Ratings are written as 0–100 and read back on the 0–10 scale, and a vinyl
  track number like "B1" is read back as side B, track 1.
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
  match still tags both rather than guessing. A place a track's lyrics
  name-drop is never linked right away — it's queued for **Review
  Song-About Places…** instead, since a common word or name (e.g. "Bath",
  "England") can match a place in your library without the lyric actually
  being about it. Tag Library Now remembers what it already scanned: a
  re-run only scans tracks whose lyrics are new or changed, and checks
  other tracks only against keywords and places you added since the last
  complete run. If you cancel a run, the next run continues the remaining
  tracks.
- **Review Song-About Places…** — approve, change, or reject each place
  lyric detection queued by Mood Tagging, grouped by place name so one
  decision covers every track currently waiting on that name. Your choice
  is saved and reused automatically — approve or reject a name once and it
  never needs reviewing again; Change lets you point a detection at a
  different place (e.g. the right "Kingston") instead. The **Match** column
  shows which specific place (with its state/country, when known) the name
  resolved to, and warns — with a live count — when more than one place in
  your library shares that name, so you can tell whether Change is needed
  before approving.
- **Artwork Conflicts…** — scans every album for tracks that disagree on
  their embedded cover art: some carrying a different picture than the
  others, or having none where the rest have one. Album art displayed
  everywhere else is read from a single track per album, so a disagreement
  can show the wrong cover for the whole album. The scan re-reads every
  audio file, so it takes a while and runs in the background with a
  progress bar. Each conflict lists the distinct versions found with a
  thumbnail and track count; **Use for all tracks** re-embeds that version
  (or, for the "No artwork" option, strips art) into every track of the
  album.
- **Find Duplicate Tracks** — run this after a big import if you suspect
  you've picked up copies of things you already had. It can match by
  metadata or by actual audio fingerprint if you want to catch re-encodes
  that don't share filenames or tags.
- **Find Missing Tracks** — checks for library entries whose file no longer
  exists on disk (moved, renamed outside the app, deleted) so you can clean
  up dangling entries.

## View Menu

- **Show Queue** (`Shift+Q`) — show or hide the [Queue Dock](#queue-dock).
- **Full Screen** (`F11`) — toggle full-screen mode.
- **Mini Player** (`Ctrl+M`) — pop out a small, always-on-top transport
  window, handy for keeping playback controls visible while you work in
  another app. Press `Ctrl+M` again to close it. It also closes when you
  close the main window.
- **Reset Layout** — snap the navigation, queue, and player docks back to
  their default positions if you've dragged something into an awkward spot.
  The queue stays shown or hidden, as it was before the reset.

## Help Menu

- **About** — version and license information.
- **Support this Project** — a link to support TrackYak's development.
- **Support Wikipedia** — TrackYak leans on Wikipedia and MusicBrainz for a
  lot of its metadata lookups; this links to Wikipedia's donation page.
