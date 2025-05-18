#!/usr/bin/env python3
"""
File Synchronizer - A tool to synchronize files from a password-protected server
with support for download resumption, progress tracking, graceful termination, regex filtering,
and interactive configuration.
"""

import os
import sys
import re
import time
import logging
import argparse
import configparser
import signal
import getpass
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple, Union
import requests
from bs4 import BeautifulSoup


# ANSI color codes for terminal output
class Colors:
    """ANSI color codes for terminal output."""

    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    PURPLE = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    BOLD = "\033[1m"

    # Background colors
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"


# Custom log formatter with colors
class ColoredFormatter(logging.Formatter):
    """Custom log formatter that adds colors based on log level."""

    FORMATS = {
        logging.DEBUG: Colors.CYAN
        + "%(asctime)s - %(levelname)s - %(message)s"
        + Colors.RESET,
        logging.INFO: Colors.GREEN
        + "%(asctime)s - %(levelname)s - %(message)s"
        + Colors.RESET,
        logging.WARNING: Colors.YELLOW
        + "%(asctime)s - %(levelname)s - %(message)s"
        + Colors.RESET,
        logging.ERROR: Colors.RED
        + "%(asctime)s - %(levelname)s - %(message)s"
        + Colors.RESET,
        logging.CRITICAL: Colors.BOLD
        + Colors.RED
        + "%(asctime)s - %(levelname)s - %(message)s"
        + Colors.RESET,
    }

    def format(self, record):
        log_format = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_format)
        return formatter.format(record)


# Set up colored logging
def setup_logging(level=logging.INFO):
    """Set up logging with colored output."""
    logger = logging.getLogger()
    logger.setLevel(level)

    # Remove any existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Create file handler
    file_handler = logging.FileHandler("sync_log.txt")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(file_handler)

    # Create console handler with colors
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(ColoredFormatter())
    logger.addHandler(console_handler)

    return logger


# Initialize logger
logger = setup_logging()


# Global variable to track if the program should terminate
terminate_requested = False


# Signal handler for graceful termination
def signal_handler(sig, frame):
    """Handle termination signals (like Ctrl+C)."""
    global terminate_requested
    if not terminate_requested:
        print(
            f"\n\n{Colors.YELLOW}Termination requested. Completing current operation...{Colors.RESET}"
        )
        terminate_requested = True
    else:
        print(
            f"\n\n{Colors.RED}Forced termination. Some files may be incomplete.{Colors.RESET}"
        )
        sys.exit(1)


# Register the signal handler
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


@dataclass
class FileInfo:
    """Data class to store file information."""

    name: str
    url: str
    size: int
    last_modified: str

    @property
    def formatted_size(self) -> str:
        """Return the file size in a human-readable format."""
        return format_size(self.size)


class ConfigManager:
    """Class to manage configuration loading and saving."""

    DEFAULT_CONFIG = {
        "SERVER": {"url": "", "username": "", "password": ""},
        "LOCAL": {"local_dir": "current_files", "download_dir": "new_downloads"},
        "DOWNLOAD": {
            "max_retries": "3",
            "retry_delay": "5",
            "chunk_size": "8192",
            "progress_update_interval": "1",
        },
        "FILTER": {
            "enabled": "false",  # Disabled by default - will not download anything
            "pattern": ".*",  # Default to match everything when enabled
            "case_sensitive": "false",
            "description": "Regular expression to filter filenames. Only files matching this pattern will be downloaded. If disabled, no files will be downloaded.",
        },
    }

    def __init__(self, config_path: str = "sync_config.ini"):
        """
        Initialize the ConfigManager.

        Args:
            config_path: Path to the configuration file
        """
        self.config_path = config_path
        self.config = configparser.ConfigParser()

    def load(self) -> configparser.ConfigParser:
        """
        Load the configuration from file. Create a default one if it doesn't exist.

        Returns:
            Configuration object
        """
        # Create default config if it doesn't exist
        if not os.path.exists(self.config_path):
            print(
                f"\n{Colors.YELLOW}Configuration file not found. Creating a new one at {self.config_path}{Colors.RESET}"
            )
            self._create_interactive_config()
        else:
            # Load the config
            self.config.read(self.config_path)

        # Check if credentials are missing and prompt for them
        self.prompt_for_missing_credentials()

        return self.config

    def _create_interactive_config(self) -> None:
        """Create a default configuration file with interactive prompts."""
        # Start with default values
        for section, options in self.DEFAULT_CONFIG.items():
            self.config[section] = options.copy()

        # Welcome message
        print(
            f"\n{Colors.BOLD}{Colors.BLUE}=== File Synchronizer - Initial Configuration ==={Colors.RESET}"
        )
        print(f"{Colors.CYAN}Let's set up your configuration file.{Colors.RESET}\n")

        # Ask for directory paths
        self._prompt_for_directories()

        # Ask for server credentials
        self._prompt_for_server_credentials()

        # Ask for filter settings
        self._prompt_for_filter_settings()

        # Save the config
        self.save()
        print(
            f"\n{Colors.GREEN}Configuration file created at {self.config_path}{Colors.RESET}"
        )

    def _prompt_for_directories(self) -> None:
        """Prompt the user for directory paths."""
        print(f"{Colors.BOLD}{Colors.BLUE}Directory Configuration:{Colors.RESET}")

        # Ask for local directory
        default_local = self.config["LOCAL"]["local_dir"]
        prompt = (
            f"Enter the path for existing files directory [default: {default_local}]: "
        )
        local_dir = input(f"{Colors.BOLD}{prompt}{Colors.RESET}").strip()
        if local_dir:
            self.config["LOCAL"]["local_dir"] = local_dir
        else:
            local_dir = default_local

        # Ensure the directory exists
        os.makedirs(os.path.abspath(local_dir), exist_ok=True)

        # Ask for download directory
        default_download = self.config["LOCAL"]["download_dir"]
        print(f"\nThe download directory is where new files will be stored.")
        prompt = (
            f"Enter the path for download directory [default: {default_download}]: "
        )
        download_dir = input(f"{Colors.BOLD}{prompt}{Colors.RESET}").strip()
        if download_dir:
            self.config["LOCAL"]["download_dir"] = download_dir
        else:
            download_dir = default_download

        # Ensure the directory exists
        os.makedirs(os.path.abspath(download_dir), exist_ok=True)

        # Show the configured paths
        print(
            f"{Colors.GREEN}Local directory: {os.path.abspath(local_dir)}{Colors.RESET}"
        )
        print(
            f"{Colors.GREEN}Download directory: {os.path.abspath(download_dir)}{Colors.RESET}"
        )

    def _prompt_for_server_credentials(self) -> None:
        """Prompt the user for server credentials."""
        print(f"\n{Colors.BOLD}{Colors.BLUE}Server Configuration:{Colors.RESET}")
        print("Please enter the credentials for the server.")

        # Ask for URL
        prompt = "Enter server URL (e.g., http://example.com/files/): "
        url = input(f"{Colors.BOLD}{prompt}{Colors.RESET}").strip()
        if url:
            self.config["SERVER"]["url"] = url

        # Ask for username
        prompt = "Enter username: "
        username = input(f"{Colors.BOLD}{prompt}{Colors.RESET}").strip()
        if username:
            self.config["SERVER"]["username"] = username

        # Ask for password
        prompt = "Enter password (input will be hidden): "
        password = getpass.getpass(f"{Colors.BOLD}{prompt}{Colors.RESET}")
        if password:
            self.config["SERVER"]["password"] = password

    def _prompt_for_filter_settings(self) -> None:
        """Prompt the user for file filter settings."""
        print(f"\n{Colors.BOLD}{Colors.BLUE}File Filter Configuration:{Colors.RESET}")
        print(
            f"{Colors.YELLOW}IMPORTANT: If filtering is disabled, no files will be downloaded.{Colors.RESET}"
        )
        print("The filter uses regular expressions to match file names.")

        # Ask if filtering should be enabled
        prompt = "Enable file filtering? (y/n) [default: n]: "
        enable_filter = input(f"{Colors.BOLD}{prompt}{Colors.RESET}").strip().lower()
        self.config["FILTER"]["enabled"] = "true" if enable_filter == "y" else "false"

        if enable_filter == "y":
            # Ask for filter pattern
            default_pattern = self.config["FILTER"]["pattern"]
            print("\nFilter pattern examples:")
            print("  .*\\.laz         - All .laz files")
            print("  G2-W08-2-.*     - Files starting with 'G2-W08-2-'")
            print("  .*-(108|109)-5  - Files containing -108-5 or -109-5")
            prompt = f"Enter filter pattern [default: {default_pattern}]: "
            pattern = input(f"{Colors.BOLD}{prompt}{Colors.RESET}").strip()
            if pattern:
                self.config["FILTER"]["pattern"] = pattern

            # Ask for case sensitivity
            prompt = "Case sensitive pattern? (y/n) [default: n]: "
            case_sensitive = (
                input(f"{Colors.BOLD}{prompt}{Colors.RESET}").strip().lower()
            )
            self.config["FILTER"]["case_sensitive"] = (
                "true" if case_sensitive == "y" else "false"
            )

            # Show configured filter
            sensitivity = (
                "case-sensitive" if case_sensitive == "y" else "case-insensitive"
            )
            print(
                f"{Colors.GREEN}Filter enabled with {sensitivity} pattern: '{self.config['FILTER']['pattern']}'{Colors.RESET}"
            )
        else:
            print(
                f"{Colors.YELLOW}Filter disabled. You must enable the filter with a pattern to download files.{Colors.RESET}"
            )
            print(
                f"{Colors.YELLOW}You can enable filtering later using command-line arguments or editing the config file.{Colors.RESET}"
            )

    def prompt_for_missing_credentials(self) -> None:
        """Check for missing server credentials and prompt the user if needed."""
        credentials_updated = False

        # Check if URL is missing or empty
        url = self.config["SERVER"].get("url", "").strip()
        if not url:
            print(f"\n{Colors.YELLOW}Server URL is not configured.{Colors.RESET}")
            url = input(
                f"{Colors.BOLD}Enter server URL (e.g., http://example.com/files/): {Colors.RESET}"
            )
            self.config["SERVER"]["url"] = url
            credentials_updated = True

        # Check if username is missing or empty
        username = self.config["SERVER"].get("username", "").strip()
        if not username:
            print(f"\n{Colors.YELLOW}Server username is not configured.{Colors.RESET}")
            username = input(f"{Colors.BOLD}Enter username: {Colors.RESET}")
            self.config["SERVER"]["username"] = username
            credentials_updated = True

        # Check if password is missing or empty
        password = self.config["SERVER"].get("password", "").strip()
        if not password:
            print(f"\n{Colors.YELLOW}Server password is not configured.{Colors.RESET}")
            password = getpass.getpass(f"{Colors.BOLD}Enter password: {Colors.RESET}")
            self.config["SERVER"]["password"] = password
            credentials_updated = True

        # Save updated credentials back to config file
        if credentials_updated:
            self.save()
            logger.info("Server credentials updated in config file")

    def save(self) -> None:
        """Save the current configuration back to the file."""
        with open(self.config_path, "w") as f:
            self.config.write(f)


class ProgressBar:
    """Class to handle progress bar visualization."""

    def __init__(self, bar_width: int = 100, update_interval: float = 1.0):
        """
        Initialize the progress bar.

        Args:
            bar_width: Width of the progress bar in characters
            update_interval: Minimum time between updates in seconds
        """
        self.bar_width = bar_width
        self.update_interval = update_interval
        self.last_update_time = 0
        self.start_time = 0

    def start(self) -> None:
        """Start the progress timer."""
        self.start_time = time.time()
        self.last_update_time = self.start_time

    def update(self, current: int, total: int, filename: str) -> None:
        """
        Update the progress bar if the update interval has elapsed.

        Args:
            current: Current progress value
            total: Total value for 100% completion
            filename: Name of the file being processed
        """
        current_time = time.time()
        if current_time - self.last_update_time >= self.update_interval:
            self._display(current, total, filename)
            self.last_update_time = current_time

    def _display(self, current: int, total: int, filename: str) -> None:
        """
        Display the progress bar.

        Args:
            current: Current progress value
            total: Total value for 100% completion
            filename: Name of the file being processed
        """
        if total <= 0:
            return

        percent = min(100.0, current / total * 100)
        elapsed_time = time.time() - self.start_time

        # Calculate speed and ETA
        if elapsed_time > 0:
            speed = current / elapsed_time
            eta = (total - current) / speed if speed > 0 else 0
            eta_str = f"{int(eta / 60)}m {int(eta % 60)}s" if eta > 0 else "N/A"
            speed_str = f"{format_size(speed)}/s"
        else:
            eta_str = "Calculating..."
            speed_str = "Calculating..."

        # Create ASCII progress bar with color
        filled_width = int(percent)

        # Apply colors to the progress bar
        if percent < 30:
            color = Colors.RED
        elif percent < 60:
            color = Colors.YELLOW
        else:
            color = Colors.GREEN

        bar = (
            color
            + "=" * filled_width
            + Colors.RESET
            + " " * (self.bar_width - filled_width)
        )

        # Format progress line
        progress_bar = f"[{bar}] {color}{percent:.1f}%{Colors.RESET}"
        info_line = f"{Colors.CYAN}{filename}{Colors.RESET}: {format_size(current)}/{format_size(total)} {speed_str} ETA: {eta_str}"

        # Update the console
        sys.stdout.write(f"\r{progress_bar}\n{info_line}")
        sys.stdout.write("\033[F")  # Move cursor up one line
        sys.stdout.flush()

    def finish(self, success: bool = True) -> None:
        """
        Complete the progress bar and add newlines.

        Args:
            success: Whether the operation completed successfully
        """
        if success:
            print(f"\n{Colors.GREEN}Download completed successfully{Colors.RESET}\n")
        else:
            print(f"\n{Colors.YELLOW}Download interrupted{Colors.RESET}\n")


class ServerParser:
    """Class to parse the server directory and extract file information."""

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        max_retries: int = 3,
        retry_delay: int = 5,
    ):
        """
        Initialize the ServerParser.

        Args:
            url: Server URL to parse
            username: Authentication username
            password: Authentication password
            max_retries: Maximum number of retry attempts on connection failure
            retry_delay: Delay between retries in seconds
        """
        self.url = url
        self.username = username
        self.password = password
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def get_files(self, file_extension: str = ".laz") -> List[FileInfo]:
        """
        Connect to server and parse the directory to get file information.

        Args:
            file_extension: File extension to filter by

        Returns:
            List of FileInfo objects
        """
        for attempt in range(self.max_retries):
            if terminate_requested:
                logger.warning("Termination requested during server connection")
                return []

            try:
                response = requests.get(self.url, auth=(self.username, self.password))
                response.raise_for_status()

                # Parse the HTML
                soup = BeautifulSoup(response.text, "html.parser")

                files = []

                # Find the table in the Apache directory listing
                table = soup.find("table")
                if not table:
                    logger.error("Could not find the file listing table in the HTML")
                    return []

                # Process each row in the table
                for row in table.find_all("tr"):
                    if terminate_requested:
                        logger.warning("Termination requested during HTML parsing")
                        return files

                    # Get all cells in the row
                    cells = row.find_all("td")

                    # Skip rows that don't have enough cells (headers, etc.)
                    if len(cells) < 4:
                        continue

                    # Get file name cell (second cell) and check if it contains a link
                    name_cell = cells[1]
                    link = name_cell.find("a")

                    if not link:
                        continue

                    filename = link.get("href")

                    # Only process files with the specified extension
                    if file_extension and not filename.lower().endswith(
                        file_extension.lower()
                    ):
                        continue

                    # Get size from the fourth cell
                    size_cell = cells[3]
                    size_text = size_cell.get_text(strip=True)
                    size_bytes = self._parse_size(size_text)

                    # Get last modified date from third cell
                    date_cell = cells[2]
                    date_text = date_cell.get_text(strip=True)

                    files.append(
                        FileInfo(
                            name=filename,
                            url=self.url + filename,
                            size=size_bytes,
                            last_modified=date_text,
                        )
                    )

                return files

            except requests.exceptions.RequestException as e:
                if terminate_requested or attempt >= self.max_retries - 1:
                    logger.error(
                        f"Error connecting to server after {attempt+1} attempts: {e}"
                    )
                    return []

                logger.warning(
                    f"Error connecting to server (attempt {attempt+1}/{self.max_retries}): {e}"
                )
                logger.info(f"Retrying in {self.retry_delay} seconds...")
                time.sleep(self.retry_delay)

    @staticmethod
    def _parse_size(size_str: str) -> int:
        """
        Parse size strings like '176M' into bytes.

        Args:
            size_str: Size string to parse

        Returns:
            Size in bytes as an integer
        """
        if not size_str or size_str == "-":
            return 0

        # Extract number and unit
        match = re.match(r"(\d+(?:\.\d+)?)(K|M|G|T)?", size_str)
        if not match:
            return 0

        size_num = float(match.group(1))
        size_unit = match.group(2) if match.group(2) else ""

        # Convert to bytes
        if size_unit == "K":
            return int(size_num * 1024)
        elif size_unit == "M":
            return int(size_num * 1024 * 1024)
        elif size_unit == "G":
            return int(size_num * 1024 * 1024 * 1024)
        elif size_unit == "T":
            return int(size_num * 1024 * 1024 * 1024 * 1024)
        else:
            return int(size_num)


class Downloader:
    """Class to handle file downloading with resume capability."""

    def __init__(
        self,
        username: str,
        password: str,
        chunk_size: int = 8192,
        max_retries: int = 3,
        retry_delay: int = 5,
        progress_update_interval: float = 1.0,
    ):
        """
        Initialize the Downloader.

        Args:
            username: Authentication username
            password: Authentication password
            chunk_size: Download chunk size in bytes
            max_retries: Maximum number of retry attempts on download failure
            retry_delay: Delay between retries in seconds
            progress_update_interval: Interval for progress updates in seconds
        """
        self.username = username
        self.password = password
        self.chunk_size = chunk_size
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.progress_bar = ProgressBar(update_interval=progress_update_interval)

        # Track download state
        self.current_file = None
        self.current_size = 0
        self.total_size = 0

    def download(self, file_info: FileInfo, local_path: str) -> Tuple[bool, int]:
        """
        Download a file with progress tracking and resumption capability.

        Args:
            file_info: FileInfo object with file details
            local_path: Path where the file should be saved

        Returns:
            Tuple of (success_status, bytes_downloaded)
        """
        # Create parent directories if needed
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        # Check if the file exists and its size
        file_exists = os.path.exists(local_path)
        downloaded_size = os.path.getsize(local_path) if file_exists else 0

        # Set current file info for tracking
        self.current_file = file_info.name
        self.current_size = downloaded_size
        self.total_size = file_info.size

        # Start download
        success, bytes_downloaded = self._download_with_retries(
            file_info, local_path, start_position=downloaded_size
        )

        # Reset tracking after download completes
        self.current_file = None

        return success, bytes_downloaded

    def _download_with_retries(
        self, file_info: FileInfo, local_path: str, start_position: int = 0
    ) -> Tuple[bool, int]:
        """
        Download with retry logic.

        Args:
            file_info: FileInfo object with file details
            local_path: Path where the file should be saved
            start_position: Byte position to start/resume download from

        Returns:
            Tuple of (success_status, bytes_downloaded)
        """
        bytes_downloaded = 0

        for attempt in range(self.max_retries):
            if terminate_requested:
                logger.warning(
                    f"Termination requested before download attempt for {file_info.name}"
                )
                self.progress_bar.finish(success=False)
                return False, bytes_downloaded

            try:
                # Set up headers for resuming download if needed
                headers = {}
                if start_position > 0:
                    headers["Range"] = f"bytes={start_position}-"
                    logger.info(f"Resuming download from byte {start_position}")

                # Open the request with authentication and possible range header
                response = requests.get(
                    file_info.url,
                    auth=(self.username, self.password),
                    headers=headers,
                    stream=True,
                    timeout=(10, 30),  # Connect timeout, read timeout
                )

                # If we're resuming and the server doesn't support range requests
                if start_position > 0 and response.status_code != 206:
                    logger.warning(
                        "Server doesn't support range requests, starting from beginning"
                    )
                    return self._download_with_retries(file_info, local_path, 0)

                response.raise_for_status()

                # Get total size from Content-Length header or Content-Range if resuming
                if response.status_code == 206:
                    # For resumed downloads with Content-Range: bytes 1000-50000/50001
                    content_range = response.headers.get("Content-Range", "")
                    total_size_match = re.search(r"/(\d+)", content_range)
                    total_size = (
                        int(total_size_match.group(1)) if total_size_match else 0
                    )
                else:
                    # For normal downloads
                    total_size = (
                        int(response.headers.get("content-length", 0)) + start_position
                    )

                # Open file in append mode if resuming, otherwise in write mode
                mode = "ab" if start_position > 0 else "wb"

                # Initialize progress bar
                self.progress_bar.start()
                current_size = start_position
                bytes_downloaded = 0

                # Download the file
                with open(local_path, mode) as f:
                    for chunk in response.iter_content(chunk_size=self.chunk_size):
                        if terminate_requested:
                            # Close file properly before exiting
                            f.flush()
                            f.close()
                            logger.warning(
                                f"Download of {file_info.name} interrupted at {format_size(current_size)}"
                            )
                            self.progress_bar.finish(success=False)

                            # Return partial success with bytes downloaded
                            return False, bytes_downloaded

                        if chunk:
                            f.write(chunk)
                            chunk_size = len(chunk)
                            current_size += chunk_size
                            bytes_downloaded += chunk_size
                            self.current_size = current_size
                            self.progress_bar.update(
                                current_size, total_size, os.path.basename(local_path)
                            )

                # Complete the progress display
                self.progress_bar.finish(success=True)

                # Verify final file size matches expected size
                final_size = os.path.getsize(local_path)
                if total_size > 0 and final_size < total_size:
                    logger.warning(
                        f"Downloaded file size ({final_size}) is less than expected ({total_size})"
                    )
                    return False, bytes_downloaded

                return True, bytes_downloaded

            except requests.exceptions.RequestException as e:
                if terminate_requested:
                    logger.warning(
                        f"Termination requested during download error handling for {file_info.name}"
                    )
                    return False, bytes_downloaded

                current_size = (
                    os.path.getsize(local_path) if os.path.exists(local_path) else 0
                )

                if attempt < self.max_retries - 1:
                    logger.warning(
                        f"Download error (attempt {attempt+1}/{self.max_retries}): {e}"
                    )
                    logger.info(
                        f"Retrying in {self.retry_delay} seconds from byte {current_size}..."
                    )
                    time.sleep(self.retry_delay)
                else:
                    logger.error(
                        f"Download failed after {self.max_retries} attempts: {e}"
                    )
                    return False, bytes_downloaded

        return False, bytes_downloaded


class FileSynchronizer:
    """Main class to synchronize files from server to local directories."""

    def __init__(self, config: configparser.ConfigParser):
        """
        Initialize the FileSynchronizer.

        Args:
            config: ConfigParser object with configuration
        """
        self.config = config
        self.url = config["SERVER"]["url"]
        self.username = config["SERVER"]["username"]
        self.password = config["SERVER"]["password"]
        self.local_dir = config["LOCAL"]["local_dir"]
        self.download_dir = config["LOCAL"]["download_dir"]

        # Load download settings
        self.max_retries = int(config["DOWNLOAD"].get("max_retries", 3))
        self.retry_delay = int(config["DOWNLOAD"].get("retry_delay", 5))
        self.chunk_size = int(config["DOWNLOAD"].get("chunk_size", 8192))
        self.progress_update_interval = float(
            config["DOWNLOAD"].get("progress_update_interval", 1)
        )

        # Initialize components
        self.server_parser = ServerParser(
            self.url, self.username, self.password, self.max_retries, self.retry_delay
        )

        self.downloader = Downloader(
            self.username,
            self.password,
            self.chunk_size,
            self.max_retries,
            self.retry_delay,
            self.progress_update_interval,
        )

        # Track synchronization state
        self.download_start_time = 0
        self.downloaded_files = []
        self.failed_files = []
        self.filtered_files = []  # Track files excluded by regex filter
        self.total_bytes_downloaded = 0

    def sync(self, file_extension: str = "") -> bool:
        """
        Synchronize files from server to local directories.

        Args:
            file_extension: File extension to filter by

        Returns:
            True if sync was successful, False otherwise
        """
        # Create directories if they don't exist
        os.makedirs(self.local_dir, exist_ok=True)
        os.makedirs(self.download_dir, exist_ok=True)

        # Reset state
        self.downloaded_files = []
        self.failed_files = []
        self.filtered_files = []
        self.total_bytes_downloaded = 0

        # Check if filtering is enabled
        filter_enabled = self.config.getboolean("FILTER", "enabled", fallback=False)

        # Alert user if filtering is disabled (no files will be downloaded)
        if not filter_enabled:
            message = (
                f"\n{Colors.BG_YELLOW}{Colors.BOLD} WARNING: File filtering is disabled! {Colors.RESET}\n"
                f"{Colors.YELLOW}No files will be downloaded unless you enable filtering.{Colors.RESET}\n"
                f"{Colors.YELLOW}Enable filtering with --enable-filter or --filter options.{Colors.RESET}\n"
            )
            print(message)
            user_response = input(
                f"{Colors.BOLD}Do you want to enable filtering with pattern '.*' (all files)? (y/n): {Colors.RESET}"
            )
            if user_response.lower() == "y":
                self.config["FILTER"]["enabled"] = "true"
                self.config["FILTER"]["pattern"] = ".*"
                print(
                    f"{Colors.GREEN}Filtering enabled with pattern '.*' to download all files{Colors.RESET}"
                )
            else:
                logger.info("Filtering remains disabled. No files will be downloaded.")
                return True

        # Get server files
        logger.info(f"Connecting to {self.url}...")
        server_files = self.server_parser.get_files(file_extension)

        if terminate_requested:
            logger.warning("Termination requested after server files listing")
            return False

        if not server_files:
            logger.error(f"Failed to get file list from server")
            return False

        logger.info(f"Found {len(server_files)} files on server")

        # Get list of local files from both directories
        local_files = self._get_local_files(file_extension)

        # Check for partial downloads in download_dir
        partial_downloads = self._identify_partial_downloads(server_files)

        # Determine files to download
        files_to_download = self._determine_files_to_download(
            server_files, local_files, partial_downloads
        )

        # Apply regex filtering - this will filter files if filtering is enabled, otherwise return no files
        files_to_download = self._apply_regex_filter(files_to_download)

        if terminate_requested:
            logger.warning("Termination requested after determining files to download")
            return False

        if not files_to_download:
            if filter_enabled:
                logger.info(
                    f"All matching files are up to date or none match the filter pattern!"
                )
            else:
                logger.info(f"Filtering is disabled. No files will be downloaded.")
            return True

        # Calculate total download size
        total_size = self._calculate_download_size(files_to_download, partial_downloads)

        # Show summary
        self._show_download_summary(files_to_download, partial_downloads, total_size)

        if terminate_requested:
            logger.warning("Termination requested after showing download summary")
            return False

        # Confirm download
        confirmation = input(
            f"{Colors.YELLOW}Continue with download? (y/n): {Colors.RESET}"
        )
        if confirmation.lower() != "y":
            logger.info("Download cancelled by user")
            return False

        # Download files
        self.download_start_time = time.time()
        success = self._download_files(files_to_download, partial_downloads)

        # Show final summary
        self._show_final_summary()

        # Return success only if all files downloaded and no termination was requested
        return success and not terminate_requested

    def _get_local_files(self, file_extension: str) -> Set[str]:
        """
        Get set of existing local files.

        Args:
            file_extension: File extension to filter by

        Returns:
            Set of filenames
        """
        local_files = set()

        # Add files from local directory
        for file in os.listdir(self.local_dir):
            if not file_extension or file.lower().endswith(file_extension.lower()):
                local_files.add(file)

        # Add files from download directory
        for file in os.listdir(self.download_dir):
            if not file_extension or file.lower().endswith(file_extension.lower()):
                local_files.add(file)

        return local_files

    def _identify_partial_downloads(
        self, server_files: List[FileInfo]
    ) -> Dict[str, Dict]:
        """
        Identify partially downloaded files.

        Args:
            server_files: List of FileInfo objects from server

        Returns:
            Dictionary mapping filenames to partial download information
        """
        partial_downloads = {}
        for file in server_files:
            local_path = os.path.join(self.download_dir, file.name)
            if os.path.exists(local_path):
                local_size = os.path.getsize(local_path)
                if local_size < file.size:
                    partial_downloads[file.name] = {
                        "local_size": local_size,
                        "server_size": file.size,
                        "percent_complete": (
                            (local_size / file.size) * 100 if file.size > 0 else 0
                        ),
                    }
        return partial_downloads

    def _determine_files_to_download(
        self,
        server_files: List[FileInfo],
        local_files: Set[str],
        partial_downloads: Dict[str, Dict],
    ) -> List[FileInfo]:
        """
        Determine which files need to be downloaded.

        Args:
            server_files: List of FileInfo objects from server
            local_files: Set of existing local filenames
            partial_downloads: Dictionary of partial download information

        Returns:
            List of FileInfo objects to download
        """
        return [
            file
            for file in server_files
            if file.name not in local_files or file.name in partial_downloads
        ]

    def _apply_regex_filter(self, files_to_download: List[FileInfo]) -> List[FileInfo]:
        """
        Apply regex filter to the files list based on configuration.

        Args:
            files_to_download: Original list of files to download

        Returns:
            Filtered list of files to download
        """
        # Check if filtering is enabled
        filter_enabled = self.config.getboolean("FILTER", "enabled", fallback=False)
        if not filter_enabled:
            # If filtering is disabled, return an empty list (no files will be downloaded)
            self.filtered_files = [file.name for file in files_to_download]
            logger.warning(f"Filtering is disabled. No files will be downloaded.")
            return []

        # Get filter pattern and options
        pattern = self.config.get("FILTER", "pattern", fallback=".*")
        case_sensitive = self.config.getboolean(
            "FILTER", "case_sensitive", fallback=False
        )

        try:
            # Compile regex with appropriate flags
            flags = 0 if case_sensitive else re.IGNORECASE
            regex = re.compile(pattern, flags)

            # Apply filter
            matching_files = []
            filtered_out = []

            for file in files_to_download:
                if regex.search(file.name):
                    matching_files.append(file)
                else:
                    filtered_out.append(file)

            # Store filtered filenames for reporting
            self.filtered_files = [file.name for file in filtered_out]

            # Report filter results
            if filtered_out:
                logger.info(
                    f"Filtered out {len(filtered_out)} files using pattern: '{pattern}'"
                )
            logger.info(
                f"Matched {len(matching_files)} files with pattern: '{pattern}'"
            )

            return matching_files

        except re.error as e:
            logger.error(f"Invalid regex pattern '{pattern}': {e}")
            logger.warning("Regex filtering disabled due to invalid pattern")
            self.filtered_files = [file.name for file in files_to_download]
            return []

    def _calculate_download_size(
        self, files_to_download: List[FileInfo], partial_downloads: Dict[str, Dict]
    ) -> int:
        """
        Calculate total size to download.

        Args:
            files_to_download: List of FileInfo objects to download
            partial_downloads: Dictionary of partial download information

        Returns:
            Total size in bytes
        """
        total_size = 0
        for file in files_to_download:
            if file.name in partial_downloads:
                # Only count remaining bytes for partial downloads
                total_size += file.size - partial_downloads[file.name]["local_size"]
            else:
                total_size += file.size
        return total_size

    def _show_download_summary(
        self,
        files_to_download: List[FileInfo],
        partial_downloads: Dict[str, Dict],
        total_size: int,
    ) -> None:
        """
        Show summary of files to be downloaded.

        Args:
            files_to_download: List of FileInfo objects to download
            partial_downloads: Dictionary of partial download information
            total_size: Total size to download in bytes
        """
        logger.info(f"Need to download {len(files_to_download)} files")

        # Show filtering info if applicable
        if self.filtered_files:
            logger.info(f"Excluded {len(self.filtered_files)} files by regex filter")

        if partial_downloads:
            logger.info(
                f"Including {len(partial_downloads)} partially downloaded files:"
            )
            for name, info in partial_downloads.items():
                percent = info["percent_complete"]
                # Color the percentage based on progress
                if percent < 30:
                    color = Colors.RED
                elif percent < 60:
                    color = Colors.YELLOW
                else:
                    color = Colors.GREEN

                # Use direct print with colors instead of logger to preserve colors
                print(
                    f"  {Colors.CYAN}{name}{Colors.RESET}: {color}{info['percent_complete']:.1f}%{Colors.RESET} complete "
                    f"({format_size(info['local_size'])} of {format_size(info['server_size'])})"
                )

        logger.info(f"Total remaining download size: {format_size(total_size)}")
        logger.info(
            f"Files will be downloaded to: {Colors.BOLD}{os.path.abspath(self.download_dir)}{Colors.RESET}"
        )
        logger.info(
            f"Press {Colors.BOLD}{Colors.YELLOW}Ctrl+C{Colors.RESET} at any time to gracefully terminate"
        )

    def _download_files(
        self, files_to_download: List[FileInfo], partial_downloads: Dict[str, Dict]
    ) -> bool:
        """
        Download the files.

        Args:
            files_to_download: List of FileInfo objects to download
            partial_downloads: Dictionary of partial download information

        Returns:
            True if all downloads were successful, False otherwise
        """
        success_count = 0

        for i, file in enumerate(files_to_download, 1):
            if terminate_requested:
                logger.warning(
                    f"Termination requested - stopping after {success_count} of {len(files_to_download)} files"
                )
                return False

            local_path = os.path.join(self.download_dir, file.name)

            # Calculate how much needs to be downloaded
            existing_size = (
                os.path.getsize(local_path) if os.path.exists(local_path) else 0
            )
            remaining_size = file.size - existing_size

            # Use colored output for file information
            print(
                f"{Colors.BOLD}File {i} of {len(files_to_download)}{Colors.RESET}: ",
                end="",
            )
            if existing_size > 0:
                percent = (existing_size / file.size) * 100
                color = (
                    Colors.GREEN
                    if percent > 60
                    else Colors.YELLOW if percent > 30 else Colors.RED
                )
                print(
                    f"Resuming {Colors.CYAN}{file.name}{Colors.RESET} ({color}{percent:.1f}%{Colors.RESET} complete, {format_size(remaining_size)} remaining)"
                )
            else:
                print(
                    f"Downloading {Colors.CYAN}{file.name}{Colors.RESET} ({file.formatted_size})"
                )

            success, bytes_downloaded = self.downloader.download(file, local_path)

            # Update tracking info
            self.total_bytes_downloaded += bytes_downloaded

            if success:
                success_count += 1
                self.downloaded_files.append(file.name)
            else:
                self.failed_files.append(file.name)
                if terminate_requested:
                    # Don't continue if termination was requested
                    break

        return success_count == len(files_to_download)

    def _show_final_summary(self) -> None:
        """Show summary of the entire download operation."""
        elapsed_time = time.time() - self.download_start_time
        hours, remainder = divmod(elapsed_time, 3600)
        minutes, seconds = divmod(remainder, 60)

        # Create a separator line
        separator = f"{Colors.BLUE}{'-' * 80}{Colors.RESET}"

        print("\n" + separator)

        # Determine completion status
        if terminate_requested:
            status_header = (
                f"{Colors.YELLOW}DOWNLOAD OPERATION TERMINATED BY USER{Colors.RESET}"
            )
        elif self.failed_files:
            status_header = (
                f"{Colors.RED}DOWNLOAD OPERATION COMPLETED WITH ERRORS{Colors.RESET}"
            )
        else:
            status_header = (
                f"{Colors.GREEN}DOWNLOAD OPERATION COMPLETED SUCCESSFULLY{Colors.RESET}"
            )

        print(f"{Colors.BOLD}{status_header}{Colors.RESET}\n")

        # Show successful downloads
        if self.downloaded_files:
            print(
                f"{Colors.GREEN}Successfully downloaded files ({len(self.downloaded_files)}):{Colors.RESET}"
            )
            for name in self.downloaded_files:
                print(f"  {Colors.CYAN}{name}{Colors.RESET}")

        # Show failed downloads
        if self.failed_files:
            print(
                f"\n{Colors.RED}Failed or incomplete files ({len(self.failed_files)}):{Colors.RESET}"
            )
            for name in self.failed_files:
                print(f"  {Colors.CYAN}{name}{Colors.RESET}")

        # Show filtered files
        if self.filtered_files:
            print(
                f"\n{Colors.YELLOW}Filtered out files ({len(self.filtered_files)}):{Colors.RESET}"
            )
            # Only show up to 10 filtered files to avoid flooding the console
            if len(self.filtered_files) > 10:
                for name in self.filtered_files[:10]:
                    print(f"  {Colors.CYAN}{name}{Colors.RESET}")
                print(
                    f"  {Colors.YELLOW}...and {len(self.filtered_files) - 10} more{Colors.RESET}"
                )
            else:
                for name in self.filtered_files:
                    print(f"  {Colors.CYAN}{name}{Colors.RESET}")

        # Show statistics
        print(f"\n{Colors.BOLD}Statistics:{Colors.RESET}")
        print(
            f"  Total downloaded: {Colors.BOLD}{format_size(self.total_bytes_downloaded)}{Colors.RESET}"
        )
        print(
            f"  Time elapsed: {Colors.BOLD}{int(hours)}h {int(minutes)}m {int(seconds)}s{Colors.RESET}"
        )

        if elapsed_time > 0 and self.total_bytes_downloaded > 0:
            avg_speed = self.total_bytes_downloaded / elapsed_time
            print(
                f"  Average download speed: {Colors.BOLD}{format_size(avg_speed)}/s{Colors.RESET}"
            )

        print(
            f"  Files are available in: {Colors.BOLD}{os.path.abspath(self.download_dir)}{Colors.RESET}"
        )

        # Show filter information
        filter_enabled = self.config.getboolean("FILTER", "enabled", fallback=False)
        if filter_enabled:
            pattern = self.config.get("FILTER", "pattern", fallback=".*")
            sensitivity = (
                "case-sensitive"
                if self.config.getboolean("FILTER", "case_sensitive", fallback=False)
                else "case-insensitive"
            )
            print(f"  Filter: {Colors.BOLD}{pattern}{Colors.RESET} ({sensitivity})")
        else:
            print(f"  Filter: {Colors.RED}Disabled{Colors.RESET}")

        # If terminated, show resume instructions
        if terminate_requested and self.failed_files:
            print(
                f"\n{Colors.YELLOW}To resume downloading incomplete files, run the program again.{Colors.RESET}"
            )
            print(
                f"{Colors.YELLOW}The program will automatically pick up where it left off.{Colors.RESET}"
            )

        print(separator)


def format_size(size_bytes: int) -> str:
    """
    Format bytes into human-readable format.

    Args:
        size_bytes: Size in bytes

    Returns:
        Formatted size string
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes/1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes/(1024*1024):.1f} MB"
    else:
        return f"{size_bytes/(1024*1024*1024):.1f} GB"


def check_color_support():
    """Check if the terminal supports colors and enable/disable accordingly."""
    # Check if we're on Windows
    if sys.platform == "win32":
        try:
            # Enable ANSI colors on Windows 10+
            from ctypes import windll

            k = windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)
            return True
        except:
            # If that didn't work, disable colors by setting them to empty strings
            for attr in dir(Colors):
                if not attr.startswith("__"):
                    setattr(Colors, attr, "")
            return False

    # For non-Windows, check if the terminal supports colors
    if not sys.stdout.isatty():
        # Not a terminal, disable colors
        for attr in dir(Colors):
            if not attr.startswith("__"):
                setattr(Colors, attr, "")
        return False

    return True


def parse_arguments() -> argparse.Namespace:
    """
    Parse command line arguments.

    Returns:
        Parsed arguments
    """
    parser = argparse.ArgumentParser(description="File Synchronizer")
    parser.add_argument(
        "-c",
        "--config",
        default="sync_config.ini",
        help="Path to configuration file (default: sync_config.ini)",
    )
    parser.add_argument(
        "-e",
        "--extension",
        default="",
        help="File extension to synchronize (e.g. '.laz'). If not specified, all files will be considered.",
    )
    parser.add_argument("-u", "--url", help="Override server URL from config file")
    parser.add_argument("--username", help="Override server username from config file")
    parser.add_argument(
        "--password",
        help="Override server password from config file (not recommended, use config file instead)",
    )
    parser.add_argument(
        "--local-dir", help="Override local directory path from config file"
    )
    parser.add_argument(
        "--download-dir", help="Override download directory path from config file"
    )
    parser.add_argument(
        "--filter",
        help="Override regex filter pattern from config file and enable filtering",
    )
    parser.add_argument(
        "--enable-filter",
        action="store_true",
        help="Enable regex filtering with pattern from config file",
    )
    parser.add_argument(
        "--disable-filter",
        action="store_true",
        help="Disable regex filtering (no files will be downloaded)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument(
        "--no-color", action="store_true", help="Disable colored output"
    )

    return parser.parse_args()


def validate_server_credentials(config: configparser.ConfigParser) -> bool:
    """
    Validate that server credentials are present and non-empty.

    Args:
        config: ConfigParser object with configuration

    Returns:
        True if all credentials are valid, False otherwise
    """
    url = config["SERVER"].get("url", "").strip()
    username = config["SERVER"].get("username", "").strip()
    password = config["SERVER"].get("password", "").strip()

    return bool(url and username and password)


def test_server_connection(url: str, username: str, password: str) -> bool:
    """
    Test the server connection with the provided credentials.

    Args:
        url: Server URL
        username: Authentication username
        password: Authentication password

    Returns:
        True if connection successful, False otherwise
    """
    try:
        response = requests.get(url, auth=(username, password), timeout=10)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to connect to server: {e}")
        return False


def main() -> int:
    """
    Main entry point for the application.

    Returns:
        Exit code
    """
    global terminate_requested

    args = parse_arguments()

    # Handle color settings
    if not args.no_color:
        check_color_support()
    else:
        # Disable colors if requested
        for attr in dir(Colors):
            if not attr.startswith("__"):
                setattr(Colors, attr, "")

    # Set up logging with appropriate level
    level = logging.DEBUG if args.verbose else logging.INFO
    setup_logging(level)

    # Print a colorful banner
    print(f"\n{Colors.BOLD}{Colors.BLUE}=== File Synchronizer ==={Colors.RESET}")
    print(
        f"{Colors.YELLOW}Press Ctrl+C at any time to gracefully terminate{Colors.RESET}\n"
    )

    try:
        # Load configuration
        config_manager = ConfigManager(args.config)
        config = config_manager.load()

        # Apply command-line overrides
        if args.url:
            config["SERVER"]["url"] = args.url
            config_manager.save()
            logger.info(f"Updated server URL in config file: {args.url}")

        if args.username:
            config["SERVER"]["username"] = args.username
            config_manager.save()
            logger.info(f"Updated server username in config file: {args.username}")

        if args.password:
            config["SERVER"]["password"] = args.password
            config_manager.save()
            logger.info("Updated server password in config file")

        if args.local_dir:
            config["LOCAL"]["local_dir"] = args.local_dir
            config_manager.save()
            logger.info(f"Updated local directory in config file: {args.local_dir}")

        if args.download_dir:
            config["LOCAL"]["download_dir"] = args.download_dir
            config_manager.save()
            logger.info(
                f"Updated download directory in config file: {args.download_dir}"
            )

        # Apply filter overrides
        if args.enable_filter:
            config["FILTER"]["enabled"] = "true"
            logger.info("Regex filtering enabled via command-line argument")

        if args.disable_filter:
            config["FILTER"]["enabled"] = "false"
            logger.info("Regex filtering disabled via command-line argument")

        if args.filter:
            config["FILTER"]["enabled"] = "true"
            config["FILTER"]["pattern"] = args.filter
            logger.info(
                f"Using regex filter pattern from command line: '{args.filter}'"
            )

        # Validate credentials
        if not validate_server_credentials(config):
            logger.error(
                "Server credentials are incomplete. Please check your configuration."
            )
            return 1

        # Test server connection
        logger.info(f"Testing connection to {config['SERVER']['url']}...")
        if not test_server_connection(
            config["SERVER"]["url"],
            config["SERVER"]["username"],
            config["SERVER"]["password"],
        ):
            logger.error(
                "Connection test failed. Please check your server credentials."
            )
            retry = input(
                f"{Colors.YELLOW}Do you want to update your server credentials? (y/n): {Colors.RESET}"
            )
            if retry.lower() == "y":
                # Reset credentials and re-prompt
                config["SERVER"]["url"] = ""
                config["SERVER"]["username"] = ""
                config["SERVER"]["password"] = ""
                config_manager.save()
                config_manager.prompt_for_missing_credentials()

                # Test again with new credentials
                if not test_server_connection(
                    config["SERVER"]["url"],
                    config["SERVER"]["username"],
                    config["SERVER"]["password"],
                ):
                    logger.error("Connection test failed again. Exiting.")
                    return 1
            else:
                return 1

        # Log filter status
        filter_enabled = config.getboolean("FILTER", "enabled", fallback=False)
        if filter_enabled:
            pattern = config.get("FILTER", "pattern", fallback=".*")
            case_sensitive = config.getboolean(
                "FILTER", "case_sensitive", fallback=False
            )
            sensitivity = "case-sensitive" if case_sensitive else "case-insensitive"
            logger.info(
                f"Regex filtering enabled with {sensitivity} pattern: '{pattern}'"
            )
        else:
            logger.warning(
                f"Regex filtering is DISABLED. No files will be downloaded unless enabled."
            )

        # Initialize synchronizer
        synchronizer = FileSynchronizer(config)

        # Perform synchronization
        if synchronizer.sync(args.extension):
            return 0
        else:
            # Check if termination was requested
            if terminate_requested:
                return 2  # Special code for user-requested termination
            return 1

    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        if logging.getLogger().isEnabledFor(logging.DEBUG):
            import traceback

            traceback.print_exc()
        return 1


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        # This should rarely be reached because we handle Ctrl+C with the signal handler
        terminate_requested = True
        print(f"\n\n{Colors.YELLOW}Process interrupted by user.{Colors.RESET}")
        sys.exit(2)
