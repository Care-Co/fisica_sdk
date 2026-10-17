"""BLE link to a Scale: bytes go to the native session, which does all framing and decoding."""
import asyncio
import concurrent.futures
import queue
import threading
import time
from bleak import BleakClient

from . import codes
from .utils import log
from .errors import FisicaTransportError


class BluetoothHandler:
    connect_timeout = 15.0  # one connection attempt
    cleanup_timeout = 2.0   # dropping a previous client before a retry
    max_retries = 3
    retry_delay = 2.0
    command_timeout = 5.0
    notify_timeout = 10.0
    settle_delay = 1.0      # kept from the field-tested sequence: pause before the first command

    def __init__(self, device_address):
        self.device_address = device_address
        self.client = None
        self.write_char = self.notify_char = None
        self.raw_queue = queue.Queue()  # bytes for the native session
        self._listening = False
        self._connected = False
        # Bleak runs on this handler's own loop, never on the application's.
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._loop.run_forever, name='fisica-ble', daemon=True)
        self._loop_thread.start()

    def _run(self, what, timeout, function, *args):
        """Await function(*args) on the BLE loop. On timeout it is cancelled; one not started yet never starts."""
        if not self._loop_thread.is_alive():
            raise FisicaTransportError('Bluetooth event loop is not running')
        expired = threading.Event()

        async def call():
            # A cancel only reaches the task after its first step, so a late start is stopped here.
            if not expired.is_set():
                return await function(*args)
        future = asyncio.run_coroutine_threadsafe(call(), self._loop)
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError:
            expired.set()
            future.cancel()
            raise FisicaTransportError(f'Bluetooth {what} timed out after {timeout:g} s') from None

    def connect_budget(self):
        """Seconds connect() waits at most: every attempt, cleanup and retry delay, plus a margin."""
        attempt = self.cleanup_timeout + self.connect_timeout + 1.0
        return self.max_retries * attempt + (self.max_retries - 1) * self.retry_delay + 5.0

    def connect(self):
        """Connect and choose the write and notify characteristics; raises FisicaTransportError on failure."""
        self._run('connection', self.connect_budget(), self._connect_async)
        log.info(f"Bluetooth connected to {self.device_address}")
        return True

    async def _connect_async(self):
        error = None
        for attempt in range(self.max_retries):
            if attempt:
                await asyncio.sleep(self.retry_delay)
            await self._drop_client()
            client = BleakClient(self.device_address, disconnected_callback=self._on_disconnected,
                                 timeout=self.connect_timeout)
            self.client = client
            try:
                await asyncio.wait_for(client.connect(), self.connect_timeout + 1.0)
            except Exception as failure:
                error = failure
                log.warning(f"Bluetooth connection attempt {attempt + 1}/{self.max_retries} failed: {failure!r}")
                continue
            try:
                self.write_char, self.notify_char = select_characteristics(client.services)
            except FisicaTransportError:
                await self._drop_client()
                raise
            self._connected = True
            return True
        raise FisicaTransportError(f'Bluetooth connection to {self.device_address} failed: {error!r}')

    async def _drop_client(self):
        client, self.client = self.client, None
        self._connected = False
        if client is not None:
            try:
                await asyncio.wait_for(client.disconnect(), self.cleanup_timeout)
            except Exception as error:
                log.debug(f"Bluetooth client cleanup failed: {error!r}")

    def _on_disconnected(self, client):
        # Called by bleak when the link drops; the receiver then lets the core recover it.
        if client is self.client:
            if self._listening:
                log.warning(f"Bluetooth link to {self.device_address} lost")
            self._connected = False
            self._listening = False

    def _on_notification(self, sender, data):
        self.raw_queue.put(bytes(data))

    async def _write(self, data):
        client = self.client
        if not self._connected or client is None:
            raise FisicaTransportError('Bluetooth link is not connected')
        await client.write_gatt_char(self.write_char, data)

    def send(self, data):
        try:
            self._run('write', self.command_timeout, self._write, bytes(data))
        except FisicaTransportError:
            raise
        except Exception as error:
            raise FisicaTransportError(f'Bluetooth write failed: {error}') from error

    def is_alive(self):
        """True while connected with notifications on; False once the link dropped or listening stopped."""
        return self._listening and self._connected and self._loop_thread.is_alive()

    def start_listening(self):
        if self._listening:
            return
        if not self._connected:
            raise FisicaTransportError('Bluetooth link is not connected')
        self._listening = True  # before subscribing, so a drop during it is not overwritten
        try:
            self._run('subscription', self.notify_timeout, self.client.start_notify, self.notify_char, self._on_notification)
        except Exception as error:
            self._listening = False
            if isinstance(error, FisicaTransportError):
                raise
            raise FisicaTransportError(f'Bluetooth notifications could not be enabled: {error}') from error
        time.sleep(self.settle_delay)

    def stop_listening(self):
        was_listening, self._listening = self._listening, False
        if was_listening and self._connected and self.client is not None:
            try:
                self._run('unsubscribe', self.command_timeout, self.client.stop_notify, self.notify_char)
            except Exception as error:
                log.debug(f"Bluetooth notifications not stopped: {error!r}")

    def close(self):
        """Stop notifications, disconnect and end the BLE loop."""
        try:
            self.stop_listening()
            if self._loop_thread.is_alive():
                self._run('disconnect', self.cleanup_timeout + 1, self._drop_client)
        finally:
            if self._loop_thread.is_alive():
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._loop_thread.join(timeout=1.0)
            if not self._loop.is_running() and not self._loop.is_closed():
                self._loop.close()


def select_characteristics(services):
    """(write, notify) characteristics of the first codes.BLE_PROFILES profile the device offers."""
    offered = {}
    for service in services:
        for characteristic in service.characteristics:
            offered.setdefault(str(characteristic.uuid).lower(), characteristic)
    for name, profile in codes.BLE_PROFILES.items():
        write, notify = offered.get(profile['write']), offered.get(profile['notify'])
        if write is not None and notify is not None:
            log.debug(f"Bluetooth profile {name}: write {write.uuid}, notify {notify.uuid}")
            return write, notify
    raise FisicaTransportError('The device offers none of the Fisica Scale BLE profiles (' +
                               ', '.join(codes.BLE_PROFILES) + ')')
