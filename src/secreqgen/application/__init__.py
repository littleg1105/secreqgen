"""Use cases of SecReqGen, shared by the command line, a GUI and an HTTP API.

Layer rule:

- The cli layer may import application, config, exceptions, typer and rich.
  It imports domain packages only inside command bodies.
- This layer may import config and exceptions. It imports domain packages
  only inside function bodies. It must not import cli, typer or rich. It must
  not print, call sys.exit, set up logging or use a default path in the
  working folder.
- Domain packages (generation, retrieval, threats, evaluation, output,
  ingestion) may import each other. They must not import application or cli.
- A GUI or an HTTP API may import application and config. It must not import
  cli.

This module imports nothing.
"""
