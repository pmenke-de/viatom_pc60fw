#!/usr/bin/env python
import sys

import asyncio
import datetime
import libscrc
import logging
from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic


class PC60FW:
    _READ_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
    _WRITE_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"

    def __init__(self, addr_str: str | None = None):
        self.addr_str = addr_str
        self.service_uuid = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
        self.timeout = 10
        self.dev = None
        self.read_service = None
        self.write_service = None
        self.quit = asyncio.Queue()
        self.stream = bytearray()
        self.logfile = None

    async def main(self):
        if self.addr_str:
            device = await BleakScanner.find_device_by_address(self.addr_str, timeout=self.timeout,
                                                               service_uuids=[self.service_uuid])
        else:
            logging.warning('Picking first device with matching service, '
                            'consider passing a specific device address, especially if there could be multiple devices')
            device = await BleakScanner.find_device_by_filter(lambda _dev, ad: True, timeout=self.timeout,
                                                              service_uuids=[self.service_uuid])
        assert device, 'No matching device found!'
        self.dev = BleakClient(device, timeout=self.timeout, disconnected_callback=self.handle_disconnect)
        logging.info(f'Trying to connect with {device}')
        await self.dev.connect()
        logging.info(f'Device {self.dev.address} connected')
        self.write_service = self.get_characteristic(self._WRITE_UUID)
        self.read_service = self.get_characteristic(self._READ_UUID)

        self.logfile = open("pc60fw.log", "a")

        await self.dev.start_notify(self.read_service, self.handle_notification)

        # await self.enable_notify()
        # await self.set_brightness(0x00)
        await self.quit.get()

    async def enable_notify(self):
        await self.dev.write_gatt_char(self.write_service, b'\xaa\x55\x0f\x84\x01', response=True)

    async def set_brightness(self, brightness: int):
        message = bytearray(b'\xaa\x55\xf0\x03\x85' + bytes([brightness]))
        message.append(libscrc.maxim8(message))
        await self.dev.write_gatt_char(self.write_service, message, response=True)

    def get_characteristic(self, uuid):
        for service in self.dev.services:
            for c in service.characteristics:
                if c.uuid == uuid:
                    return c
        assert False, f'Characteristic {uuid} not found!'

    def handle_notification(self, sender: BleakGATTCharacteristic, data: bytearray):
        logging.debug(f'Notification from {sender}: {data.hex()}')
        self.stream.extend(data)
        self.process_messages()

    def handle_disconnect(self, client: BleakClient):
        logging.warning(f'Device {client.address} disconnected')
        self.quit.put_nowait(True)

    def process_messages(self):
        while True:
            if len(self.stream) == 0:
                break
            # search for sync sequence
            idx = self.stream.find(b'\xaa\x55')
            # gather more bytes if the sync sequence not found
            if idx < 0:
                break
            # check if there are enough bytes to read the message length
            # otherwise skip and gather more bytes
            if len(self.stream) >= idx + 4:
                length = self.stream[idx + 3]
                # check whether all the bytes of the message available
                # otherwise skip and gather more bytes
                if len(self.stream) >= idx + 4 + length:
                    # remove the bytes from the stream prior sync
                    # (if any - as this should not happen except in case of the first message)
                    del self.stream[0: idx]
                    # copy the whole message
                    message = self.stream[0: idx + 4 + length]
                    # the last byte of the message is a CRC8/MAXIM
                    # the CRC sum for the whole message (including the CRC) must be 0
                    if libscrc.maxim8(message) != 0:
                        logging.warning("CRC error")
                    logging.debug(f"Message: {message.hex()}")
                    # remove the sync bytes and the CRC
                    message = message[2: idx + 3 + length]
                    # remove the processed bytes from the stream
                    del self.stream[0: idx + 4 + length]
                    # messages with 0x08 on the second spot contains values appear on the OLED display
                    if message[2] == 0x01:
                        logging.info("SpO2: %d PR: %d PI: %1.1f" % (message[3], message[4], message[6] / 10))
                        self.logfile.write("%d\t%d\t%d\t%1.1f\n" % (datetime.datetime.now().timestamp(),
                                                                    message[3], message[4], message[6] / 10))
                        self.logfile.flush()
                else:
                    break
            else:
                break


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(PC60FW(sys.argv[1] if len(sys.argv) == 2 else None).main())
