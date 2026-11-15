"""PostgreSQL connection with a pure-Python fallback when native libpq cannot load."""

import importlib
import logging

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def create_database_engine(url, **kwargs):
    url = make_url(url)
    if url.drivername == "postgresql+psycopg":
        try:
            importlib.import_module("psycopg")
        except ImportError:
            # Do not relax Windows policies or modify DLLs. Use another supported driver.
            # Unknown connection options (especially SSL settings) must never be discarded.
            if set(url.query) - {"options"}:
                raise
            importlib.import_module("pg8000")
            url = url.set(drivername="postgresql+pg8000")
            logging.getLogger(__name__).warning("Native PostgreSQL driver unavailable; using pg8000")
    if url.drivername == "postgresql+pg8000":
        args = dict(kwargs.pop("connect_args", {}))
        if "connect_timeout" in args:
            args["timeout"] = args.pop("connect_timeout")
        if "options" in url.query:
            args["startup_params"] = {**args.get("startup_params", {}), "options": url.query["options"]}
            url = url.difference_update_query(["options"])
        kwargs["connect_args"] = args
    return create_engine(url, **kwargs)
