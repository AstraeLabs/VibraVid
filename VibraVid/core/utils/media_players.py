# 17.01.26

import logging
import os

logger = logging.getLogger(__name__)


class MediaPlayers:
    """Creates `.ignore` and `.plexignore` files when downloads start and removes them when cleanup runs."""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.ignore_files = [os.path.join(self.output_dir, ".ignore"), os.path.join(self.output_dir, ".plexignore")]

    def create(self) -> None:
        try:
            os.makedirs(self.output_dir, exist_ok=True)
        except Exception:
            logger.info(f"Could not ensure output dir exists: {self.output_dir}")

        for f in self.ignore_files:
            try:
                with open(f, "w", encoding="utf-8") as fh:
                    fh.write("")
            except Exception as e:
                logger.error(f"Failed to create ignore file {f}: {e}")

    def remove(self) -> None:
        for f in self.ignore_files:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception as e:
                logger.error(f"Failed to remove ignore file {f}: {e}")
