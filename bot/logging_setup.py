import logging
import sys


def configure_logging(level: int = logging.INFO) -> None:
    """Send levelled, timestamped logs to stderr.

    The bot used bare print() in every module: no levels, no timestamps, and
    no way to tell a routine message from a swallowed exception. systemd
    captures stderr into the journal, so `journalctl -u CU_statistics_bot`
    now shows severities and times.
    """
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # aiogram and apscheduler are chatty at INFO.
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.executors").setLevel(logging.WARNING)
