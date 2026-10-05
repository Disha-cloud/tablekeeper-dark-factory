# Tablekeeper stage 4: seating changes and recurring amendments

Build and start (no manual setup, no network needed at run time; all HTML, JS and CSS are
served from the image, with system fonts only):

```
docker build -t tablekeeper-stage4 stage-4 && docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage4
```

Run from the repository root. The service listens on `0.0.0.0:$PORT` (default 8080).

- Browser UI: `/` (search and availability grid), `/signup`, `/login`, `/lookup`.
- API: see the stage 1-4 specifications; `GET /health` returns `200 {"status":"ok"}`.

Self-tests (need the container running on port 8080):

- Stage 1/2 API regression: `python3 stage-4/selftest/run.py`
- Stage 3 API regression: `python3 stage-4/selftest/s3.py`
- Stage 4 API (planner vs. brute force, closures, series amend, schema 4): `python3 stage-4/selftest/s4.py`
- UI regression (headless browser, Playwright for Python): `python stage-4/selftest/ui.py`
- Upgrade from the stage-1/2/3 images (ports 8081-8083, see the script header): `python3 stage-4/selftest/upgrade.py`
