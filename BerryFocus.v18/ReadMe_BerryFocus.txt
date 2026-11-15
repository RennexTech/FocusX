BerryFocus v1  -  Deep Work Protocol + Timetable
=================================================

SETUP
  1. Put BerryFocus.py, build_berryfocus.bat, Berry2.ico, cherry.wav, beep.wav in one folder.
  2. pip install -r requirements.txt
  3. Before first run, remove the OLD app's scheduler entry:  FocusXv17.exe --uninstall
  4. Double-click build_berryfocus.bat  ->  dist\BerryFocus.exe
  5. Run it once. It registers itself in Task Scheduler (starts at logon + 2-min watchdog).

YOUR DATA
  dist\BerryFocusData\berryfocus.db   history, plans, progress, settings (SQLite)
  dist\BerryFocusData\audio\          your own audio files (copied in when you add them)
  Moving PC: copy BerryFocus.exe + the BerryFocusData folder. That's all.

POMODORO
  - Auto-starts 5s after launch (default 40 focus / 1 rest) unless you touch anything.
  - Focus 20-120 min, rest 1-15 min. Your last values are remembered.
  - 20s before the break: cherry.wav plays, pause/reset are disabled.
  - Break: black fullscreen, keyboard + mouse locked, Task Manager killed. Shows NEXT UP.
  - Break ends: beep.wav, next session starts, and a check-in box asks what you covered.

TIMETABLE (fullscreen, Esc to close)
  - Each tab is a plan (e.g. "Python - Day 1"). One row = one focus session.
  - ADD ROW, or ADD BLOCK ("Python", 4 sessions, goal "20 pages") to create 4 rows at once.
  - Topic / Goal / Progress type-and-forget: autosaved to SQLite.
  - AUDIO per row: + picks a .wav/.mp3 (e.g. from Kokoro), play button previews it.
    It plays after that row's session, either when the break starts or when it ends
    (choose on the Focus tab).
  - USE FOR POMODORO makes that tab the active plan. Each finished session moves to the next row.
  - DUPLICATE AS NEW DAY copies topics/goals/audio with progress cleared: record audio once, reuse daily.
  - Check-in: untick "Goal reached" and that block repeats later instead of being marked done.

PROGRESS TAB
  Daily session goal, monthly hours goal with the hours/day you still need, day streak,
  14-day chart, recent sessions with your notes.

SLEEP TIMER
  - Bedtime choices 10:00 PM - 12:00 AM (default 11:30 PM).
  - First pick of the day locks in: afterwards you can only move it EARLIER.
  - At bedtime: 10s notice, input locked, Windows shutdown. Launching between 12 AM and 5:30 AM
    gets the same treatment. The X button only minimises. Task Scheduler relaunches if killed.

TEST FLAGS
  BerryFocus.exe --dry-run                    never shuts down, separate test database
  BerryFocus.exe --dry-run --fake-now 23:29   pretend it is 11:29 PM
  BerryFocus.exe --uninstall                  remove the Task Scheduler entry and exit
