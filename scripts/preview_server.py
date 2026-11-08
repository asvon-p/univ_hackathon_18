"""Preview child process: enforce the no-dotenv rule before importing either app."""

import os
import sys

from scripts.frontend_preview import deny_dotenv

if __name__ == "__main__":
    os.environ["FITJANG_SKIP_DOTENV"] = "1"
    sys.addaudithook(deny_dotenv)
    import uvicorn

    uvicorn.run(sys.argv[1], host="127.0.0.1", port=int(sys.argv[2]), access_log=False, log_level="warning")
