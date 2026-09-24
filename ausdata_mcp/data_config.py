"""Public-source configuration, independent of the retired app runtime."""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

if not os.getenv("PYTHON_DOTENV_DISABLED"):
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")


class DataSettings(BaseModel):
    abs_api_base: str = "https://data.api.abs.gov.au"
    worldbank_base_url: str = "https://api.worldbank.org/v2"
    imf_base_url: str = "https://www.imf.org/external/datamapper/api/v1"
    oecd_base_url: str = "https://sdmx.oecd.org/public/rest"
    comtrade_base_url: str = "https://comtradeapi.un.org/data/v1/get"
    comtrade_api_key: str | None = None
    pdh_base_url: str = "https://stats-sdmx-disseminate.pacificdata.org/rest"
    macro_timeout_seconds: int = Field(default=120, ge=1, le=600)
    python_binary: str = sys.executable


@lru_cache(maxsize=1)
def get_data_settings() -> DataSettings:
    names = {
        "abs_api_base": "ABS_API_BASE",
        "worldbank_base_url": "WORLDBANK_BASE_URL",
        "imf_base_url": "IMF_BASE_URL",
        "oecd_base_url": "OECD_BASE_URL",
        "comtrade_base_url": "COMTRADE_BASE_URL",
        "comtrade_api_key": "COMTRADE_API_KEY",
        "pdh_base_url": "PDH_BASE_URL",
        "macro_timeout_seconds": "MACRO_TIMEOUT_SECONDS",
        "python_binary": "PYTHON_BINARY",
    }
    values = {field: os.environ[name] for field, name in names.items() if os.getenv(name)}
    if "macro_timeout_seconds" not in values and os.getenv("MACRO_TIMEOUT"):
        values["macro_timeout_seconds"] = os.environ["MACRO_TIMEOUT"]
    return DataSettings(**values)
