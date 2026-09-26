"""Run one local bot instance, keeping a consistent backup before startup."""
import atexit
import msvcrt
import os
from pathlib import Path
import sqlite3
from datetime import datetime, timezone

from bot.main import load_config, main

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
data = ROOT / "data"
data.mkdir(exist_ok=True)
lock = (data / "local-bot.lock").open("a+b")
lock.seek(0)
try:
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
except OSError:
    raise SystemExit("Shoe Bot is already running. Do not start another copy.")

pid_file = data / "local-bot.pid"
pid_file.write_text(str(os.getpid()), encoding="utf-8")
atexit.register(lambda: pid_file.unlink(missing_ok=True))
config = load_config()
if config.database_path.exists():
    backups = data / "backups"
    backups.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    with sqlite3.connect(config.database_path.as_uri() + "?mode=ro", uri=True) as source:
        with sqlite3.connect(backups / f"shoe-bot-{stamp}.sqlite3") as dest:
            source.backup(dest)
main()
