import os

import uvicorn

if __name__ == "__main__":
    try:
        port = int(os.environ.get("PORT", "8080"))
    except ValueError:
        port = 8080
    uvicorn.run("app:app", host="0.0.0.0", port=port, workers=1, access_log=False,
                log_level="warning", backlog=2048, timeout_keep_alive=30)
