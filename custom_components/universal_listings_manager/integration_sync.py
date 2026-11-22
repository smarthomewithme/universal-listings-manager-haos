"""Synchronize only the ULM Marketplace integration after the ULM app updates.

Never edits files, installs other integrations, or restarts HA Core. Marketplace
continues to control downloads and asks for a Core restart when it is required.
"""

import asyncio
import logging
from datetime import timedelta

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval

from .client import BridgeError
from .sync_policy import may_sync

_LOGGER = logging.getLogger(__name__)
MAX_ATTEMPT_INTERVAL = 60 * 60


def start_integration_sync(hass, client):
    """Start a bounded, per-installation sync loop; return its unload callback."""
    lock = asyncio.Lock()
    last_attempt = 0.0
    tasks = set()

    async def check():
        nonlocal last_attempt
        if lock.locked():
            return
        async with lock:
            try:
                backend = await client.status()
            except BridgeError:
                return  # Never update from an unverified app version.

            registry = er.async_get(hass)
            for state in hass.states.async_all("update"):
                entity = registry.async_get(state.entity_id)
                if entity is None or entity.platform != "marketplace":
                    continue
                attrs = state.attributes
                if not may_sync(
                    state=state.state,
                    installed_version=attrs.get("installed_version"),
                    latest_version=attrs.get("latest_version"),
                    app_version=backend.version,
                    release_url=attrs.get("release_url"),
                ):
                    continue

                now = asyncio.get_running_loop().time()
                if now - last_attempt < MAX_ATTEMPT_INTERVAL:
                    return
                last_attempt = now
                _LOGGER.info("Synchronizing the ULM Marketplace integration from a verified release")
                try:
                    await hass.services.async_call(
                        "update", "install", {"entity_id": state.entity_id}, blocking=True
                    )
                except Exception:
                    _LOGGER.exception("ULM Marketplace synchronization failed; leaving HA Core unchanged")
                return

    def spawn_check(_now=None):
        task = hass.async_create_background_task(check(), "ULM Marketplace version synchronization")
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    unsubscribe = async_track_time_interval(hass, spawn_check, timedelta(minutes=15))
    spawn_check()

    def stop():
        unsubscribe()
        for task in tuple(tasks):
            task.cancel()

    return stop
