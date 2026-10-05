# The factory

Four AI agents ("seats") built Tablekeeper stages 1–4 in a single BAND room,
`tablekeeper-graded` (room id `cb9b0698-d896-4160-bd3b-0318c212c7d6`). The full, unfiltered
room export is [`room.json`](room.json): 675 messages from `2026-10-05T02:44:53.009Z` to
`2026-10-05T03:59:09.280Z`. Every timestamp below comes from that file. Every
test count comes from a harness `report.json` under `band-work/checks/`.

## 1. Seat setup

All four seats run on **Claude Code**. The model for each seat comes from the `Model:`
line of its mandate file in [`mandates/`](mandates/).

| Seat | Mandate file | Harness | Model (mandate file) | Role |
|---|---|---|---|---|
| architect | `mandates/architect.md` | Claude Code | Opus 5.5 | Lead. Dispatches work, writes decisions and handoffs, keeps each accepted stage in its own buildable folder, never asks the human anything |
| developer | `mandates/developer.md` | Claude Code | Sonnet 5 | Implements one scoped work item per handoff, from the written spec only. Never reads test files |
| reviewer | `mandates/reviewer.md` | Claude Code | Sonnet 5 | Reviews each committed revision against the full handoff requirements, not the implementer's claims. Never implements |
| release-verifier | `mandates/release-verifier.md` | Claude Code | Sonnet 5 | Re-runs every check personally, trusts no reported pass, and confirms acceptance only on a genuine pass |

The commit trailers in this repository don't fully match the mandate files. The developer's
commits (e.g. `cf904c2`, `fe7547c`, `74fb95b`, `d6266e7`) carry
`Co-Authored-By: Claude Sonnet 5.5`, but `mandates/developer.md` says `Sonnet 5`. The
architect's commits carry `Co-Authored-By: Claude Opus 5.5`, which matches its mandate. The
reviewer and release-verifier made no commits, so for them the mandate file is the only
recorded source.

## 2. Design rationale

**Why this split.** Each seat has a separate duty, and no seat signs off its own work.
- The developer builds from the spec plus the architect's decisions, and is barred from the
  shipped tests (D11, E9, F12, G7). Its code therefore has to meet the written requirements
  rather than be tuned to pass a test suite.
- The reviewer checks the committed revision against the complete requirements. It runs the
  service and probes it directly, rather than reading the developer's report.
- The release-verifier is a second, independent gate. It does a clean `docker build
  --no-cache`, runs the harness in isolated mode, re-runs the developer's self-tests, and
  checks git integrity, i.e. that earlier stage folders are unchanged.
- The architect is the only seat that talks to the human and the only one that settles
  ambiguity. Every open point becomes a numbered decision in `decisions/stage-N.md`:
  D1–D14 for stage 1, E1–E12 for stage 2, F1–F14 for stage 3, G1–G10 for stage 4.
  Decisions the developer made on its own are only adopted once the architect accepts them
  (F14, G10).

**How handoffs work.** Every handoff is self-contained. The architect mandate requires
"every handoff you send contains the complete task and requirements in full — never a
pointer to a room message". In practice, each handoff in `room.json` embeds the full stage
spec, all decisions to date, and the exact acceptance criteria. Sizes by stage:

| Stage | Developer handoff | Review handoff | Release handoff |
|---|---|---|---|
| 1 | 32,797 | 33,379 | 34,394 |
| 2 | 56,981 | 57,554 | 56,998 |
| 3 | 82,420 | 84,031 | 83,211 |
| 4 | 98,442 | 99,908 | 98,975 |

(characters)

Copies are kept in [`handoffs/`](handoffs/) (14 files). Each stage then runs the same chain:
1. The architect copies the previous stage folder into the new one and commits the copy on
   its own.
2. The architect posts the developer handoff.
3. The developer commits and posts its revision.
4. The architect sends a review handoff.
5. The reviewer posts ACCEPT or REJECT.
6. On REJECT, the architect sends a fix handoff to the developer and a re-review follows.
7. On ACCEPT, the architect sends a release handoff and the release-verifier posts ACCEPT
   or REJECT.
8. The architect records the acceptance in `decisions/stage-N.md`, commits it and reports
   the stage done.

**Why mandates stay generic.** Each of the four mandates is two lines long: a harness/model
header and one paragraph about the role's behaviour. None mentions tablekeeper, a stage, an
endpoint or a test. Everything track-specific reaches the seats through handoffs and decision
files. The same mandates were posted once at the start, and the seats then carried all four
stages with no change to their instructions. Swapping the track or the spec changes the
handoffs and leaves the mandates as they are.

## 3. Measured cost and time per stage

### Wall-clock time (from `room.json`)

A stage starts at the human's dispatch message and ends at the architect's
"Graded Stage N is done" message.

| Stage | Dispatch (UTC) | Architect handoff | Developer build | Review (incl. any fix rounds) | Release verification | Report done | **Total** |
|---|---|---|---|---|---|---|---|
| 1 | 02:45:21.720 | 31.2 s | 6m 17.8s | 3m 40.4s | 1m 24.7s | 22.2 s | **12m 16.3s** |
| 2 | 02:59:15.283 | 40.1 s | 10m 50.5s | 9m 08.2s | 2m 21.1s | 20.7 s | **23m 20.6s** |
| 3 | 03:23:13.266 | 35.6 s | 7m 20.8s | 4m 52.5s | 3m 38.3s | 20.9 s | **16m 48.1s** |
| 4 | 03:40:33.811 | 39.5 s | 7m 59.4s | 4m 55.5s | 4m 33.4s | 21.9 s | **18m 29.7s** |

Notes on the table:
- **Architect handoff** runs from dispatch to the developer handoff being posted.
- **Developer build** runs from the handoff to the developer's "implemented and committed"
  post.
- **Review** runs to the reviewer's final ACCEPT. For Stage 2 it includes the reject → fix →
  re-review cycle in §4.
- **Release verification** runs from the review ACCEPT to the release-verifier's ACCEPT.
- **Report done** runs to the architect's stage-complete message.

The four stages add up to 70m 54.7s. Between stages the room waited for the next human
dispatch: 1m 37.3s, 37.4s and 32.4s.

### Harness results per stage (from `report.json`)

These are the release-verifier's acceptance runs, each in isolated mode:

| Stage | Report | Revision | Stage 1 | Stage 2 | Stage 3 | Stage 4 | Claimed | Harness runtime |
|---|---|---|---|---|---|---|---|---|
| 1 | `checks/graded-s1-release-r1-1` | `e82f15e` | 120/120 | — | — | — | 1 | 25.8 s |
| 2 | `checks/graded-s2-release-r1-1` | `471f686` | 120/120 | 25/25 | — | — | 2 | 28.9 s |
| 3 | `checks/graded-s3-release-r1-1` | `462b287` | 120/120 | 25/25 | 7/7 | — | 3 | 34.2 s |
| 4 | `checks/graded-s4-release-r1-1` | `189986c` | 120/120 | 25/25 | 7/7 | 6/6 | 4 | 35.6 s |

Every run listed shows 0 failed, 0 errors and 0 skipped.

The final `--all` confirmation, `checks/graded-all-final2/`, ran on revision `b7e5a9f` in
host mode against the working tree:

| Folder | Stages passed | Counts | `highest_contiguous` | `share` | Runtime |
|---|---|---|---|---|---|
| `stage-1/` | 1 | 120/120 | 1 | 1.0 | 34.1 s |
| `stage-2/` | 1, 2 | 120/120, 25/25 | 2 | 1.0 | 29.9 s |
| `stage-3/` | 1, 2, 3 | 120/120, 25/25, 7/7 | 3 | 1.0 | 30.8 s |
| `stage-4/` | 1, 2, 3, 4 | 120/120, 25/25, 7/7, 6/6 | 4 | 1.0 | 31.9 s |

An earlier attempt at the same confirmation, `checks/graded-all-final/`, recorded
`state: error` for every stage. Its `startup.log` shows `docker build failed: ... failed to
connect to the docker API` because the Docker daemon wasn't running. That was an
environment problem, not a code failure, and `graded-all-final2` is the rerun.

`band-work/checks/` also holds `graded-*` reports dated 2026-10-04, at revisions such as
`c81aad8`, `a1911b4` and `0d4afe2`. Those commits aren't in this repository, so the reports
belong to an earlier run and are not counted here.

### Cost

**Not measured.** The harness `report.json` files record revisions, pass/fail counts and
start/finish times, but no token, model-usage or monetary fields. `room.json` doesn't record
usage either. No cost figure is given because none can be sourced.

## 4. Proof the factory catches bad work: the Stage 2 rejection

**The submission.** At 03:10:45.940 the developer posted Stage 2 as committed at
**`fe7547c`** (commit time 03:10:23Z). It reported its API, upgrade and Playwright UI
self-tests as "FAILURES: none".

**The shipped checks did not catch the defect.** The reviewer's own harness run on that
revision, `checks/graded-s2-review-r1-1` (revision `abfc721`, which contains `fe7547c`),
passed stage 1 at 120/120 and stage 2 at 25/25, and claimed stage 2.

**How the reviewer found it.** At 03:15:57.381 the reviewer posted *"REVIEW of graded Stage 2
fe7547c: REJECT, one defect."*
- It drove the UI in headless Chromium at 375, 320 and 1280 px and saw the literal text
  `null` rendered in the site header on every signed-out screen (`/`, `/signup`, `/login`,
  `/lookup`): on its own line at 375 px, and top-right on desktop.
- It confirmed this in the DOM: `#site-header` had a text node `"null"`, and its `innerText`
  ended in `...Sign up\nnull`.
- It traced the cause. `renderHeader()` in `static/app.js` called
  `hd.replaceChildren(..., session ? h('div'...) : null)`, and `replaceChildren`
  stringifies a `null` argument into a text node. The file's own `h()` helper skips nulls,
  but this call bypassed it.
- It classed the defect against the stage-2 "Product and visual direction" requirement.
- Everything else it checked passed: combinations, E6 validation order, a 50-way concurrency
  probe, upgrade from stage 1, and every UI flow.

**The fix.** At 03:16:14.536 the architect sent the developer "Graded Stage 2 fix round 1".
At 03:17:09.314 the developer posted the fix, committed as **`74fb95b`** (commit time
03:16:59Z) with the message *"Stage 2 fix r1: never pass null to replaceChildren (stray 'null'
in header); add no-junk-text UI check"*. The diff touches 2 files, with 38 insertions and 4
deletions:
- `stage-2/static/app.js`:
  - Adds a `kids()` helper that flattens its arguments and drops `null`, `undefined` and
    `false`.
  - Routes both `replaceChildren` calls that could receive `null` through it: the one in
    `renderHeader()`, and a second one the developer found in the lookup detail view
    (`errText ? errBox(errText) : null`).
- `stage-2/selftest/ui.py`: adds a check for stray `null`, `undefined`, `NaN` or
  `[object Object]` text in `body` and `#site-header`. It covers all four routes, signed out
  and signed in, at 375 and 1280 px.

**The re-review.** At 03:17:29.013 the architect sent a re-review handoff for `74fb95b`. At
03:19:54.098 the reviewer posted *"RE-REVIEW of graded Stage 2 74fb95b: ACCEPT. DEFECT 1 is
fixed, I found no regression, and I found no new defect."*
- It grepped every other `replaceChildren` and `append` call in `app.js`.
- It ran a text scan for `null`, `undefined`, `NaN` and `[object` across all signed-out,
  signed-in, lookup, booking, error, uncertain and no-slots states at 375 px.
- It re-ran its r1 UI suites.
- It disclosed one gap rather than hiding it: at 1280 px the scan aborted at the confirmation
  step, so the confirmation, uncertain, error and no-slots states were not scanned at
  that width.
- Its harness run `checks/graded-s2-review-r2-1` (revision `ece05e8`) passed stage 1 at
  120/120 and stage 2 at 25/25.

**Release.** At 03:22:15.216 the release-verifier posted *"ACCEPT graded Stage 2, commit
74fb95b"*. Its isolated harness run `checks/graded-s2-release-r1-1` passed 120/120 and 25/25
and claimed stage 2. It also re-ran the developer's `ui.py`, including the new "no stray
null/undefined text" check, and reported "FAILURES: none".

**Cycle time.** From the developer's first submission to the review REJECT took 5m 11.4s.
From the REJECT to the posted fix took 1m 11.9s. From the fix to the re-review ACCEPT took
2m 44.8s. In git: `fe7547c` → `abfc721` (E12 clarification + review handoff) → `4e2017d`
("review r1 REJECT; developer fix round 1 handoff") → `74fb95b` (fix) → `ece05e8` (r2 review
handoff) → `471f686` (review r2 ACCEPT) → `aa8e33c` ("Graded Stage 2 accepted by review and
release verification at 74fb95b").

The other three stages were accepted at their first revision: Stage 1 at `cf904c2`, Stage 3
at `ba2bcfc` and Stage 4 at `d6266e7`. Review raised non-blocking notes on Stage 1; the
architect recorded them as D14 deferrals and resolved them in Stage 2 as E11.

## 5. Total run

| Event | Timestamp (UTC, `room.json`) |
|---|---|
| First event in the room (seats join; room creation itself is not in the export) | 2026-10-05 02:44:53.009 |
| First human message (architect mandate) | 02:45:00.096 |
| Stage 1 dispatch | 02:45:21.720 |
| Stage 4 accepted by release-verifier | 03:58:41.612 |
| Architect reports "Graded Stage 4 is done" | 03:59:03.546 |

- **Total duration:** 74m 10.5s, from the first room event to the architect's Stage 4
  completion report. Measured from the first human message, it is 74m 03.4s.
- **Human input:** exactly 8 messages, all from the user `Chiraanth` (the only
  `senderType: "User"` entries in `room.json`).
  - 4 seat mandates, posted between 02:45:00.096 and 02:45:15.456.
  - 4 stage dispatches, at 02:45:21.720, 02:59:15.283, 03:23:13.266 and 03:40:33.811.
- **Other human input:** none. `room.json` contains no other human message: no
  clarification, approval or correction. The agents' 667 other messages include 23 text
  posts from the architect, 6 each from the developer and reviewer, and 5 from the
  release-verifier. The rest are tool calls and results, thoughts, task events and join
  events.
- **Result:** four accepted stages, at `cf904c2`, `74fb95b`, `ba2bcfc` and `d6266e7`. One
  review rejection was caught and fixed, and no release-verifier rejections occurred. The
  final `--all` confirmation (`graded-all-final2`, revision `b7e5a9f`) passed 120/120, 25/25,
  7/7 and 6/6, and each folder claimed its own stage.
