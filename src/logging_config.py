
import logging
import os
import sys
from logging.handlers import RotatingFileHandler


def configure_logging(log_level=logging.DEBUG, log_file="agent.log"):


    logger = logging.getLogger()
    logger.setLevel(log_level)


    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


    try:
        file_handler = RotatingFileHandler(
            log_file, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception:
        pass


    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    log_path = os.path.abspath(log_file)
    logging.info(f"Logging initialized. Detailed logs written to: {log_path}")

