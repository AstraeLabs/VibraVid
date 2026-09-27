# 03.03.24

import logging
from typing import Any

from rich.console import Console
from rich.prompt import Prompt
from rich.text import Text

from .message import start_message
from .shared_styles import TableStyle, create_styled_table

logger = logging.getLogger(__name__)
TABLE_STYLE = TableStyle.MODERN_ROUNDED


class TVShowManager:
    def __init__(self):
        """
        Initialize TVShowManager with default values.
        """
        self.console = Console()
        self.tv_shows: list[dict[str, Any]] = []
        self.slice_start = 0
        self.slice_end = 10
        self.step = self.slice_end
        self.column_info = []
        self.table_style = TABLE_STYLE

    def add_column(self, column_info: dict[str, dict[str, str]]) -> None:
        """
        Add column information.

        Parameters:
            column_info (Dict[str, Dict[str, str]]): Dictionary containing column names, their colors, and justification.
        """
        self.column_info = column_info

    def add_tv_show(self, tv_show: dict[str, Any]) -> None:
        """
        Add a TV show to the list of TV shows.

        Parameters:
            tv_show (Dict[str, Any]): Dictionary containing TV show details.
        """
        if tv_show:
            self.tv_shows.append(tv_show)

    def display_data(self, data_slice: list[dict[str, Any]]) -> None:
        """
        Display TV show data in a tabular format.

        Parameters:
            data_slice (List[Dict[str, Any]]): List of dictionaries containing TV show details to display.
        """
        if not data_slice:
            logger.error("Nothing to display.")
            return 404

        if not self.column_info:
            logger.error("Error: Column information not configured.")
            return 404

        table = create_styled_table(self.table_style)

        # Add columns dynamically based on provided column information
        for col_name, col_style in self.column_info.items():
            color = col_style.get("color", "white")
            width = col_style.get("width", None)
            justify = col_style.get("justify", "center")
            header_color = col_style.get("header_color", None)
            if header_color:
                col_name = Text(col_name, style=header_color)

            table.add_column(
                col_name,
                style=color,
                justify=justify,
                width=width,
                no_wrap=col_style.get("no_wrap", False),
                overflow=col_style.get("overflow", "ellipsis"),
            )

        # Add rows dynamically based on available TV show data
        for _idx, entry in enumerate(data_slice):
            if entry:
                row_data = []
                for col_name in self.column_info.keys():
                    value = str(entry.get(col_name, ""))
                    max_len = self.column_info[col_name].get("max_length", None)
                    if max_len and len(value) > max_len:
                        value = value[: max_len - 3] + "..."
                    row_data.append(value)

                table.add_row(*row_data)

        self.console.print(table)

    def run(self) -> str:
        """
        Run the TV show manager application.

        Returns:
            str: Last command executed before breaking out of the loop.
        """
        if not self.tv_shows:
            logger.error("Error: No data available for display.")
            return ""

        if not self.column_info:
            logger.error("Error: Columns not configured.")
            return ""

        total_items = len(self.tv_shows)
        last_command = ""

        while True:
            start_message()

            # Check and adjust slice indices if out of bounds
            current_slice = self.tv_shows[self.slice_start : self.slice_end]
            if not current_slice and total_items > 0:
                self.slice_start = 0
                self.slice_end = min(self.step, total_items)
                current_slice = self.tv_shows[self.slice_start : self.slice_end]

            result_func = self.display_data(current_slice)
            if result_func == 404:
                logger.error("Error displaying data. Exiting.")
                return ""

            # Add page info below the table
            page_info = f"[dim][{self.slice_start + 1}-{min(self.slice_end, total_items)} of {total_items}][/dim]"
            self.console.print(page_info, justify="center")

            # Pagination prompt
            self.console.print("[green]Press [red]Enter [green]for next page, [red]'q' [green]to quit.")

            prompt_msg = "\n[cyan]Insert media index [yellow]'1'[cyan], [yellow]'*' [cyan]to download all media, [yellow]'1-2' [cyan]for a range of media or [yellow]'3-*' [cyan]to download from a specific index to the end"
            key = Prompt.ask(prompt_msg)

            last_command = key

            if key.lower() in ["q", "quit"]:
                break
            elif key == "":
                if self.slice_end < total_items:
                    self.slice_start += self.step
                    self.slice_end += self.step
                    if self.slice_end > total_items:
                        self.slice_end = total_items
                else:
                    self.slice_start = 0
                    self.slice_end = self.step
            else:
                break

        return last_command

    def clear(self) -> None:
        """
        Clear all TV shows data.
        """
        self.tv_shows = []
        self.slice_start = 0
        self.slice_end = self.step
