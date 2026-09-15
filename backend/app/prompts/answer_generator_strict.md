---
name: answer_generator_strict
version: 2026-09-15.1
purpose: Stricter retry instructions appended after a grounding-validation failure.
---
Your previous answer failed automated verification against the evidence. The problems found are listed in <validation_feedback>.

Produce a new answer that fixes every problem:
- Remove any statement that is not word-for-word supported by the evidence you cite.
- Use numbers, dates, names and contact details exactly as written in the evidence.
- Cite, for each claim, the specific source that contains it.
- Prefer a shorter answer containing only clearly supported claims.
- If you cannot produce a fully supported answer, abstain.
