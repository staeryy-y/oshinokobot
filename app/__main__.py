from __future__ import annotations

import logging
import sys

import uvicorn

from .admin.server import create_app
from .config import ConfigError, load_config
from .logging_setup import configure_logging


def main() -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        logging.basicConfig(stream=sys.stderr, level=logging.ERROR)
        logging.getLogger("oshinokobot").error(str(exc))
        raise SystemExit(1) from exc

    configure_logging(config.log_path)

    app = create_app(config)
    # log_config=None: keep our own stdout+file handlers as the only ones;
    # uvicorn's default logging setup would otherwise install its own.
    uvicorn.run(app, host=config.host, port=config.port, log_config=None)


if __name__ == "__main__":
    main()
