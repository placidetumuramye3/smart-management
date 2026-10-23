# ESP32 School RFID Reader

This sketch sends card UIDs to the Flask application's existing RFID endpoint:

```text
POST /student/rfid-login
Content-Type: application/json
{"uid":"A1B2C3D4"}
```

The app records attendance for a registered student and queues an unknown UID for office registration.

## Assumed hardware

- ESP32 DevKit
- RC522 (MFRC522) RFID reader
- 16x2 LCD with an I2C backpack
- Three LEDs and three 220-330 ohm resistors

If your RFID reader is not an RC522 or the LCD is not an I2C model, the pin mapping and libraries need to change.

## Wiring

| Module pin | ESP32 pin |
| --- | --- |
| RC522 SDA / SS | GPIO 5 |
| RC522 SCK | GPIO 18 |
| RC522 MOSI | GPIO 23 |
| RC522 MISO | GPIO 19 |
| RC522 RST | GPIO 4 |
| RC522 3.3V | 3V3 |
| RC522 GND | GND |
| LCD SDA | GPIO 21 |
| LCD SCL | GPIO 22 |
| LCD VCC / GND | Match the LCD module requirements / GND |
| Red LED anode through resistor | GPIO 25 |
| Yellow LED anode through resistor | GPIO 26 |
| Green LED anode through resistor | GPIO 27 |
| Each LED cathode | GND |

The RC522 is a 3.3 V device. ESP32 GPIOs are not 5 V tolerant. If a 5 V LCD backpack pulls SDA/SCL up to 5 V, use a bidirectional I2C logic-level shifter or safe 3.3 V pull-ups. Connect all grounds together.

LCD backpacks commonly use address `0x27`; some use `0x3F`. Change `LCD_ADDRESS` in the sketch if needed.

## Configure and upload

1. Install the Espressif ESP32 board package in Arduino IDE.
2. Install these libraries from Library Manager: **MFRC522**, **LiquidCrystal I2C**, and **ArduinoJson**.
3. Open `esp32_school_rfid.ino` and set `WIFI_SSID` and `WIFI_PASSWORD`.
4. Find the computer's LAN IPv4 address with `ipconfig`. Replace `192.168.1.50` in `RFID_ENDPOINT` with that address. Keep port `5000` and the `/student/rfid-login` path.
5. Keep the ESP32 and Flask server on the same trusted Wi-Fi/LAN. Start the app with `python run.py`; the current launcher listens on port 5000. Allow private-network access through the computer firewall if prompted.
6. Select the matching ESP32 board and port, upload, and open Serial Monitor at `115200` baud.

Do not use `localhost` or `127.0.0.1` in `RFID_ENDPOINT`: those addresses would point to the ESP32 itself. This sketch uses plain HTTP for a trusted local network. Do not expose port 5000 to the public internet; production deployment should use HTTPS and device authentication.

## LED and LCD feedback

- **Green:** scan accepted; LCD shows the student and attendance status, or meeting check-in.
- **Yellow:** card UID reached the server but is not linked to a student; it is queued for office registration.
- **Red:** scan denied/outside the attendance window, Wi-Fi or server error, or card not allowed.
- **No LED:** ready to scan.

An unknown card must first be linked to a student by authorized school staff in the web app. The server response and UID are also printed to Serial Monitor for troubleshooting.
