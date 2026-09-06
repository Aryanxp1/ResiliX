"""ResiliX command-line interface package.

The CLI is a thin interface layer over the existing Controller, engines,
scenarios, safety system, metrics and analysis modules. It does not
reimplement any platform logic; it only wires user input into the existing
components and renders their output for the terminal.
"""



def __getattr__(name):
    if name == "main":
        from .main import main
        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["main"]
