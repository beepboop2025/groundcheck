"""Run the engine: `python -m groundcheck_engine`."""
import uvicorn

from . import config

if __name__ == "__main__":
    uvicorn.run("groundcheck_engine.app:app", host=config.HOST, port=config.PORT, log_level="info")
