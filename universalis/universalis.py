from typing import Optional, TypeVar, Union
import logging
import time
from pydantic import BaseModel
import requests
from PySide6.QtCore import QMutex
from cache import Persist, persist_to_file

from universalis.models import Listings

_logger = logging.getLogger(__name__)

GET_CONTENT_RATE = 0.05
get_content_time = time.time() - GET_CONTENT_RATE

universalis_mutex = QMutex()


def _get_listings(id: int, world: Union[int, str]) -> Listings:
    url = f"https://universalis.app/api/v2/{world}/{id}"
    global get_content_time
    now_time = time.time()
    if now_time - get_content_time < GET_CONTENT_RATE:
        time.sleep(GET_CONTENT_RATE - now_time + get_content_time)
    get_content_time = time.time()
    content_response = requests.get(url)
    while content_response.status_code != 200:
        time.sleep(0.05)
        _logger.log(logging.WARN, f"Error code {content_response.status_code}")
        content_response = requests.get(url)
    content_response.raise_for_status()
    return Listings.parse_obj(content_response.json())


get_listings = Persist(_get_listings, "listings.json", 3600, Listings)


def save_to_disk() -> None:
    get_listings.save_to_disk()
