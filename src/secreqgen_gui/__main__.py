"""Start the GUI: python -m secreqgen_gui."""

from __future__ import annotations

import logging

from secreqgen_gui.settings import (
    listens_on_this_computer_only,
    prepare_environment,
    server_host,
    server_port,
)

NETWORK_WARNING = (
    "The GUI listens on %s. It has no login. Every computer that can reach "
    "this address can start runs and read the files that the GUI can read."
)


def main() -> None:
    """Start the server. It listens on 127.0.0.1 unless the settings say more."""
    prepare_environment()
    if not listens_on_this_computer_only():
        logging.getLogger(__name__).warning(NETWORK_WARNING, server_host())
    from nicegui import ui

    from secreqgen_gui import app

    app.setup()
    ui.run(
        app.root,
        host=server_host(),
        port=server_port(),
        reload=False,
        show=False,
        title="SecReqGen",
    )


if __name__ == "__main__":
    main()
