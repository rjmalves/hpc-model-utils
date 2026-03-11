import logging
import logging.handlers
import sys
from multiprocessing import Process
from os import getenv
from typing import Optional

from app.utils.singleton import Singleton


class Log(metaclass=Singleton):
    listener: Optional[Process] = None

    @classmethod
    def configure_logger(cls) -> logging.Logger:
        root = logging.getLogger("hpc-model-utils")
        f = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
        # Logger para ARQUIVO
        h = logging.handlers.RotatingFileHandler(
            "./hpc-model-utils.log", "a", 10000, 0, "utf-8"
        )
        h.setFormatter(f)
        # Logger para STDOUT
        std_h = logging.StreamHandler(stream=sys.stdout)
        std_h.setFormatter(f)
        root.addHandler(std_h)
        root.addHandler(h)
        root.setLevel(getenv("LOGLEVEL", "INFO").upper())
        return root
