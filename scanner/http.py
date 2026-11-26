import logging
import time

import requests

log = logging.getLogger("scanner")
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "memecoin-scanner/1.0"


def get_json(url, params=None, headers=None, timeout=20, retries=2):
    for attempt in range(retries + 1):
        try:
            r = SESSION.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 429:
                time.sleep(2 + attempt * 3)
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            if attempt == retries:
                log.warning("GET %s failed: %s", url.split("?")[0], e)
                return None
            time.sleep(1 + attempt)
    return None


def post_json(url, payload, headers=None, timeout=60):
    try:
        r = SESSION.post(url, json=payload, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("POST %s failed: %s", url.split("?")[0], e)
        return None


def get_text(url, timeout=20):
    try:
        r = SESSION.get(url, timeout=timeout)
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        log.warning("GET %s failed: %s", url.split("?")[0], e)
        return None
