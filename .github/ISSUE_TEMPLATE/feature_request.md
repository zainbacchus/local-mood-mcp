---
name: Feature request
about: Suggest an improvement
labels: enhancement
---

**What problem does this solve?**

**Proposed behavior**

**Determinism check:** can this be expressed as a pure function of
`(Track, Context)`? Selections must stay reproducible — same library + same
parameters → same ordered track IDs.

**API check:** does this need any endpoint that is deprecated or `403` for new
apps (genres, popularity, audio-features, recommendations, related-artists,
batch reads)? If so, it can't be built — see the table in the README.
