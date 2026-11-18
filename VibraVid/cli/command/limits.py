# 25.07.26

from VibraVid.core.ui.tracker import context_tracker


def add_limit_arguments(target) -> None:
    """Register --max-segments / --max-time on an ArgumentParser or argument group."""
    target.add_argument(
        "--max-segments",
        dest="max_segments",
        type=str,
        default=None,
        metavar="N|START-END",
        help='Limit download to first N segments, or a START-END range, e.g. "50" or "10-50" (HLS/DASH/ISM)',
    )
    target.add_argument(
        "--max-time",
        dest="max_time",
        type=str,
        default=None,
        metavar="SEC|HH:MM:SS|START-END",
        help='Limit downloaded duration, e.g. "00:05:00", 300, or a range "00:01:00-00:05:00" (HLS/DASH/ISM)',
    )


def apply_limits(args) -> None:
    """Propagate the parsed limits to context_tracker, where the downloaders pick them up."""
    context_tracker.max_segments = getattr(args, "max_segments", None)
    context_tracker.max_time = getattr(args, "max_time", None)
