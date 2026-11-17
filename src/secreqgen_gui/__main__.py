"""Start the GUI: python -m secreqgen_gui."""

from __future__ import annotations

from secreqgen_gui.settings import prepare_environment, server_host, server_port


def main() -> None:
    """Start the server. It listens on 127.0.0.1 unless the settings say more."""
    prepare_environment()
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
