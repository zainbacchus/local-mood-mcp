## What does this change?

## Checklist

- [ ] `pytest -q` passes (synthetic data, no network needed)
- [ ] New or changed scoring behavior has a test
- [ ] Selection stays deterministic (pure functions of `Track` + `Context`)
- [ ] No deprecated Spotify endpoints introduced; every scope still maps to an endpoint actually called
- [ ] No tokens logged; no personal data committed
