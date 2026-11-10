"""NFC reader for tool stickers: a PN532 module on the Pi's I2C pins (the NullLab board).

Only the sticker's built-in serial number (UID) is read, like reading the number stamped on a key instead of
writing anything onto it, so any NFC sticker works and nothing has to be programmed. An instructor links a
sticker to a tool once at the kiosk (Instructor > tool > Link NFC sticker).
"""


class Pn532Reader:
    """Wiring: VCC to pin 1 (3.3 V), GND to pin 6, SDA to pin 3, SCL to pin 5; the board's switches set to I2C.
    Needs I2C turned on (install.sh does it) and the kiosk user in the i2c group."""

    def __init__(self):
        import board
        import busio
        from adafruit_pn532.i2c import PN532_I2C

        self.pn532 = PN532_I2C(busio.I2C(board.SCL, board.SDA), debug=False)
        self.pn532.SAM_configuration()  # wake it and turn on the card-reading mode

    def read(self, timeout: float = 0.5) -> str | None:
        """Waits up to `timeout` seconds for a sticker; returns its UID as hex (04A1B2C3D4E5F6) or None."""
        uid = self.pn532.read_passive_target(timeout=timeout)
        return uid.hex().upper() if uid else None
