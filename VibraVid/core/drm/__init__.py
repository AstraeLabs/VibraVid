# 10.01.26

__all__ = ["DRMManager"]


def __getattr__(name):
    if name == "DRMManager":
        from .manager import DRMManager

        return DRMManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
