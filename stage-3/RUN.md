# Tablekeeper stage 2: online booking and combined tables

Build and start (no manual setup, no network needed at run time; all HTML, JS and CSS are
served from the image, with system fonts only):

```
docker build -t tablekeeper-stage2 stage-2 && docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage2
```

Run from the repository root. The service listens on `0.0.0.0:$PORT` (default 8080).

- Browser UI: `/` (search and availability grid), `/signup`, `/login`, `/lookup`.
- API: see the stage 1 and stage 2 specifications; `GET /health` returns `200 {"status":"ok"}`.

Self-tests (need the container running on port 8080):

- API: `python3 stage-2/selftest/run.py`
- UI (headless browser, Playwright for Python): `python stage-2/selftest/ui.py`
- Upgrade (stage-1 export into stage 2): `python3 stage-2/selftest/upgrade.py`
