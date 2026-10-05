# Tablekeeper stage 3: booking policies, history and recurring reservations

Build and start (no manual setup, no network needed at run time; all HTML, JS and CSS are
served from the image, with system fonts only):

```
docker build -t tablekeeper-stage3 stage-3 && docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage3
```

Run from the repository root. The service listens on `0.0.0.0:$PORT` (default 8080).

- Browser UI: `/` (search and availability grid), `/signup`, `/login`, `/lookup`.
- API: see the stage 1, 2 and 3 specifications; `GET /health` returns `200 {"status":"ok"}`.

Self-tests (need the container running on port 8080):

- Stage 1/2 API regression: `python3 stage-3/selftest/run.py`
- Stage 3 API: `python3 stage-3/selftest/s3.py`
- UI regression (headless browser, Playwright for Python): `python stage-3/selftest/ui.py`
- Upgrade from the stage-1 and stage-2 images (ports 8081 / 8082, see the script header): `python3 stage-3/selftest/upgrade.py`
