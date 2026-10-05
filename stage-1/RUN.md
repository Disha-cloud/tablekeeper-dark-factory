# Tablekeeper stage 1

Build and start (no manual setup, no outbound network needed at run time):

```
docker build -t tablekeeper-stage1 stage-1 && docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage1
```

Run from the repository root. The service listens on `0.0.0.0:$PORT` (default 8080);
`GET /health` returns `200 {"status":"ok"}` as soon as the process is up.

Self-test (needs the container running on port 8080): `python3 stage-1/selftest/run.py`
