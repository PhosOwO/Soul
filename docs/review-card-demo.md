# Review Card Demo

This demo uses a sandbox project that represents one real engineering thread: implementing Soul's Review Card as a low-interruption confirmation queue. It is meant for screenshots or a short recording that shows how Review Card choices affect future AI context.

## Video

<video src="media/demo.mp4" controls width="100%"></video>

If the video does not render in your Markdown viewer, open [docs/media/demo.mp4](media/demo.mp4) directly.

## Setup

```powershell
soul review mock --reset
soul --review --review-project-dir .soul/sandboxes/review-mock --review-port 8766 --review-restart
```

Open:

```text
http://127.0.0.1:8766/review
```

The mock contains a small queue:

- ready to confirm: Review Card product purpose, candidate filtering, evidence display, and the two-bucket UI shape
- needs review: one conflict with an older dashboard-style design and one open DSH before-turn hook question
- evidence refs: expandable from each card

## Context Comparison

Capture the baseline context before making decisions:

```powershell
Push-Location .soul/sandboxes/review-mock
soul context --limit 20
Pop-Location
```

Then use the Review Card UI:

1. Accept `Review Card is a low-interruption confirm and needs-review queue, not a project dashboard.`
2. Accept `Review Card should only surface high-value confirm or needs-review decisions.`
3. Accept `Review Card UI should show two decision buckets: Ready to Confirm and Needs Review.`
4. Reject the older dashboard-style conflict if it should no longer guide implementation.
5. Leave or reject the DSH before-turn hook question if it is not settled.

Capture context again:

```powershell
Push-Location .soul/sandboxes/review-mock
soul context --limit 20
Pop-Location
```

Expected effect:

```text
Before decisions:
- Soul Current State has little accepted Review Card implementation guidance.
- Review Card shows pending confirmations.

After Accept:
- Accepted statements appear in Soul Current State.
- Future agent turns can receive the accepted Review Card constraints by default.

After Reject:
- The rejected item leaves the Review Card queue.
- It does not enter Accepted State and should not affect future agent turns.
```

## Screenshot Set

Recommended screenshots:

1. Review Card queue with multiple pending decisions.
2. One card expanded to show evidence refs.
3. The queue after accepting one item and rejecting one item.
4. Terminal before/after comparison of `soul context --limit 20`.

## Recording Script

Keep the recording short:

```text
0:00  Open Review Card mock.
0:05  Show the queue: ready to confirm vs needs review.
0:15  Expand evidence for one card.
0:25  Accept the settled Review Card behavior.
0:35  Reject the older dashboard-style conflict.
0:45  Compare soul context before and after.
```

The point to show is not that Review Card summarizes the project. It only asks for a few high-quality decisions; accepted decisions change AI context, rejected decisions do not.
