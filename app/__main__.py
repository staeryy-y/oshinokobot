from __future__ import annotations

import uvicorn

from .admin.server import create_app
from .config import load_config
from .logging_setup import configure_logging


def main() -> None:
    config = load_config()
    configure_logging(config.log_path)

    app = create_app(config)
    # log_config=None: keep our own stdout+file handlers as the only ones;
    # uvicorn's default logging setup would otherwise install its own.
    uvicorn.run(app, host=config.host, port=config.port, log_config=None)


if __name__ == "__main__":
    main()
