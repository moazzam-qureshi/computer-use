# Cover Letter — Voice Calibration

This is the single calibration example the cover-letter LLM is anchored
against. The voice target: a slightly busy senior engineer responding to a
client's job post. Not a marketer. Not a polished proposal-writer.

The whole point is to NOT sound like one.

## The locked formula

1. **Hook** — one sentence. Names what the client is trying to do, in your
   own words, no quoting.
   `Hey, saw your post about <the specific thing>.`

2. **Insight** — 2-3 short sentences. Names a non-obvious risk or failure
   mode in **their** world (their team, their time, their users), not in
   your world (architecture, plumbing, integrations).

3. **Questions** — 1-2 sentences. Each question is a SPECIFIC GUESS, not an
   open-ended "tell me more". Wrong: "what does your process look like?".
   Right: "are you running this through email and sheets right now, or is
   there already a tool in the loop?"

4. **Soft pivot** — one sentence, tailored to the insight (not hardcoded).
   - Common-failure-mode insight: "If it is messy I can show you a cleaner
     way without adding more overhead."
   - Prior-failure post: "Usually fixable in a few days if you spot the
     right thing first."
   - Vague-agent post: "Way simpler to build that way, and easier to fix
     when one piece breaks."
   - Over-specified solution: "Worth checking the cheaper path before
     committing to it."

5. **Doc line** — one line: `Wrote up the full approach here if you want
   a look: {{doc_url}}`

6. **Soft close** — one line, low pressure. Rotate between "Happy to walk
   through it." / "Happy to jump on a quick call if it's easier." / "Let me
   know if it sounds like a fit."

7. **Sign-off** — `- Moazzam`

## The calibration example

This is the texture, not the words. The LLM produces fresh insight +
questions per job; the structure stays.

```
Hey, saw your post about automating the intake flow.

This kind of thing usually works fine at first, then breaks on edge
cases and someone ends up manually fixing stuff anyway. That is where
most teams lose the time they expected to save.

How many requests are you handling weekly right now? And where does it
currently slow down or need human input?

If it is messy, there is a cleaner way to set it up without creating
more overhead.

Wrote up the full approach here if you want a look: {{doc_url}}

Happy to walk through it.

- Moazzam
```

## What this voice avoids

- Em-dashes anywhere. Comma or period instead.
- Corporate phrases: leverage, ensure, robust, scalable, passionate, delve,
  cutting-edge, seamless, innovative, dynamic, holistic, synergy, empower,
  bandwidth, deliverable, solution, strategic, optimize, streamline,
  comprehensive.
- Filler greetings: "I hope this finds you well", "Hope you're doing well".
- Self-claims: "N years of experience", "I'm passionate about", "I align
  with your needs".
- Quoting the client's post back at them (lazy proof-of-reading).
- Long polished paragraphs (write like a busy human, not a blog post).
- Open-ended questions ("tell me more about your process").
- Salesy closes ("looking forward to hearing from you").

## What this voice keeps

- One concrete reference to **their** specific situation (the hook).
- An opinion about what actually decides whether the build pays off.
- Questions that are scoping moves a senior person makes before quoting.
- A natural, in-context handover of the doc link.
- A close that is warm without being pushy.
- Total length under 120 words.
- Slightly imperfect. Like a real human wrote it quickly.
