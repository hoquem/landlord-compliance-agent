# Product

## Register

product

## Users

**Now:** Mahmudul Hoque, an experienced developer running a twelve-property
portfolio across nine entities and seven banks. He knows what an SA105
category is. He is not confused by a table.

**Context, in one sentence:** he is at a desk in a home office in the
evening, lamp on, room dim, working through a few hundred bank lines in
hour-long bursts somewhere in the fortnight before a quarterly deadline.
That sentence decides the theme, and it is why the default is dark.

**Later:** other landlords. The MVP has one user, but the design should not
need rebuilding to gain a second. That rules out anything that only works
because the operator wrote the code.

**The job:** turn a pile of bank exports into a quarterly MTD ITSA update
that is *right*, without personally checking three hundred lines twice.

## Product Purpose

Parse bank statements, propose HMRC categories, let a human confirm them,
apportion by ownership share, and produce per-entity quarterly export packs.

Success is not "the return was filed." Success is **the return was filed and
no figure in it was a guess.** Every number on an export traces back to a
line a person confirmed. The product's job is to make that tractable, not to
make it feel effortless.

## Brand Personality

**Precise, calm, candid.**

Warmth lives in two places and nowhere else: a handful of earned moments,
and the voice. Copy speaks plainly and always says what to do next.
*"Nothing to review. Your next update is due 7 November."* Not *"No
results."* Never jokey, because the subject is somebody's tax return.

## Anti-references

- **Generic SaaS dashboard.** Hero metric, four stat cards, a gradient
  accent, a chart nobody reads. This product has no vanity metric worth
  putting in 48pt.
- **Xero / QuickBooks.** Dense with the wrong density: crowded toolbars,
  dated chrome, a modal for everything, accountant-first vocabulary.
- **gov.uk / HMRC.** Correct and joyless. Being right is the floor here,
  not the achievement.

Not an anti-reference: consumer fintech's *warmth*. Its playfulness is
wrong, its humanity is not.

## Design Principles

1. **Refusing is a feature, so make refusal feel like protection.** The most
   important thing this system does is decline to produce a number it cannot
   stand behind: an export blocks while a single line is unreviewed, and a
   filed quarter whose history changed raises rather than guessing. The UI
   must name the offending rows and link straight to them. A refusal that
   reads as an obstacle is a UI failure, not a product one.

2. **The screen gets calmer as the work gets done.** Attention is the scarce
   resource. Settled work recedes to neutral; only what still needs a
   decision holds colour. Clearing a review queue should visibly drain the
   screen of accent. That is the core loop's reward, and it is built into
   the colour system rather than bolted on as a celebration.

3. **The machine proposes, the human decides, and the UI never blurs that.**
   No auto-confirmation at any confidence. A proposal is styled as a
   suggestion awaiting a decision, never as a fact. Confidence changes
   **what you see first**, never what appears true.

4. **Say what happened and what to do next.** The backend fails loudly on
   purpose: the parser names the row that broke, the export names the
   transactions blocking it. The UI must carry that specificity through
   rather than flattening it to "something went wrong." An error that does
   not tell you your next action has wasted the backend's honesty.

5. **Density is respect.** Three hundred lines and an hour. Screen space
   spent on decoration is screen space taken from the work.

## Accessibility & Inclusion

- **Keyboard-complete (required).** Every action reachable without a mouse,
  visible focus rings, the review queue fully drivable from the keyboard.
  This is also simply the fastest way to clear three hundred rows.
- **Reduced motion (already built, spec-mandated).** `Motion.of(context)`
  returns `Duration.zero` under `MediaQuery.disableAnimations`; every
  animation including `flutter_animate` list entrances goes through it.
- **Status is never colour alone.** Every state is paired with its word.
  Each status already *has* a name in the domain, so showing it costs
  nothing, and colour-only status would not survive the second user.
- WCAG AA is the floor for text; the palette in DESIGN.md was checked
  numerically rather than by eye, and most pairs clear AAA.
