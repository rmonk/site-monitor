import ctypes
import ctypes.util
import logging
import socket
import threading
import time
from typing import Optional

logger = logging.getLogger("site_monitor.resolver")

_last_reload_time: float = 0.0
_reload_lock = threading.Lock()


def reload_dns_resolver(force: bool = False) -> bool:
    """
    Forces glibc to re-read /etc/resolv.conf without requiring a process restart.

    In Linux environments (such as glibc in Docker containers), the resolver
    initializes its nameserver configuration into process memory on the first
    lookup and does not automatically notice changes to /etc/resolv.conf.
    Calling res_init() re-initializes the resolver state in memory.

    Debounced to at most once every 2.0 seconds unless force=True to prevent
    redundant reloads during concurrent monitor checks.
    """
    global _last_reload_time

    now = time.time()
    with _reload_lock:
        if not force and (now - _last_reload_time < 2.0):
            return True

        try:
            libc_name = ctypes.util.find_library("c") or "libc.so.6"
            libc = ctypes.CDLL(libc_name)

            res_init_fn = getattr(libc, "__res_init", getattr(libc, "res_init", None))
            if res_init_fn is not None:
                res_init_fn()
                _last_reload_time = now
                logger.info(
                    "Successfully reloaded DNS resolver state via glibc res_init()."
                )
                return True
            else:
                logger.debug("res_init symbol not found in standard C library.")
                return False
        except Exception as e:
            logger.warning(f"Failed to reload DNS resolver via res_init: {e}")
            return False


def is_dns_error(exc: Optional[BaseException]) -> bool:
    """
    Determines if an exception was caused by a DNS / hostname resolution failure.
    Recursively inspects the exception chain (__cause__ and __context__).
    """
    visited = set()
    curr: Optional[BaseException] = exc

    dns_error_phrases = (
        "name or service not known",
        "temporary failure in name resolution",
        "getaddrinfo failed",
        "nodename nor servname provided",
        "eai_again",
        "eai_noname",
        "err_name_not_resolved",
        "non-recoverable failure in name resolution",
    )

    while curr is not None and id(curr) not in visited:
        visited.add(id(curr))

        if isinstance(curr, socket.gaierror):
            return True

        msg = str(curr).lower()
        if any(phrase in msg for phrase in dns_error_phrases):
            return True

        curr = curr.__cause__ or curr.__context__

    return False
