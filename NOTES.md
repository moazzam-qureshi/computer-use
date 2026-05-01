# Project notes / deferred improvements

## Upwork driver — section-anchored panel scraper (deferred)

**Why:** current `collect_panel_info` uses heuristics (longest text = description,
hyperlinks → tags). Works but mixes signals — e.g. "Client's recent history"
section bleeds reviewer names into tags, and the "Activity on this job"
section's past-job titles can pollute description capture.

**Idea:** walk elements top-to-bottom (sort by y-coord) and segment them
into known sections by detecting header text. Stop collecting at "Client's
recent history" — everything below is noise.

Section anchors observed in the panel (verified via 3 screenshots):

| Section | Anchor text |
|---|---|
| Header  | (top — title hyperlink) |
| Meta    | "Posted N <unit> ago", "Worldwide" |
| Body    | "Summary" / "Description:" |
| Budget  | "$X.XX Fixed-price" OR "$X.XX-$Y.YY Hourly" + duration + experience |
| Contract-to-hire | "Contract-to-hire opportunity" |
| Project type | "One-time project" / "Ongoing project" |
| Skills  | "Skills and Expertise" / "Mandatory skills" → chips before "Preferred qualifications" |
| Activity | "Activity on this job" → "Proposals: N to M", "Interviewing: N", etc. |
| Client history | "Client's recent history" → STOP COLLECTING |

Right column (parallel structure):
- "Apply now" / "Save job" (don't touch)
- "Send a proposal for: N Connects" — useful signal: high Connects (16+) often
  correlates with multi-expert-tag jobs.
- "Available Connects: N"
- "About the client" → payment verified, rating, jobs posted, total spent,
  avg hourly rate paid, member since
- "Job link" textbox + "Copy to clipboard" button

**Distinguishing job hourly vs client avg hourly:**
- Job hourly (when applicable): "$X.XX-$Y.YY /hr Hourly" — appears in left/center
  column, near the budget chip.
- Client history: "$66.16 /hr avg hourly rate paid" — explicit "/hr avg" suffix,
  appears in right "About the client" column.

**Bonus signal to capture:** "Send a proposal for: N Connects" — extract N.
High Connects cost ≥ 16 is a positive quality signal on Upwork.

## Other deferred items

- Search query support: input a search term, the driver navigates to
  Find Work → Search and processes results. Same panel-handling logic.
- Saved jobs tab — same workflow, different feed source.
- Pagination beyond what infinite-scroll exposes — Upwork eventually requires
  clicking "Show more" or a numbered page. Need to detect end-of-feed.
