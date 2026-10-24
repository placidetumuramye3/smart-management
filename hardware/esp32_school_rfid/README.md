# ESP32 School RFID Reader

This sketch sends card UIDs to the Flask application's existing RFID endpoint:

```text
POST /student/rfid-login
Content-Type: application/json
X-Device-Token: <same token configured on the Flask server and ESP32>
{"uid":"A1B2C3D4"}
```

The app records attendance for a registered student and queues an unknown UID for office registration.

The server's configured attendance times are authoritative. `late_after` is the allowed-time end: scans through one minute after it are Present; scans more than one minute and up to 15 minutes after it are Late. If no scan arrives by the end of the 15-minute grace, the student is saved as Absent. After that grace, the attendance endpoint returns Access Denied and the card scan is not recorded as an arrival; the separate afternoon scan window remains available. The LCD shows the status returned by the server, and the red LED lights for Access Denied. Each card scan is matched to its registered student, so the LCD also shows that student's own class attendance summary after accepted scans. The attendance register and Excel export show the full date/time for accepted scans, including legacy rows that stored only the clock time. The ESP32 checks the attendance-status endpoint immediately after connecting and every 30 seconds after that; each successful check reconciles absences and queues in-app alerts for newly absent students, guardians, and authorized staff.

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
3. Open `esp32_school_rfid.ino` and set `WIFI_SSID`, `WIFI_PASSWORD`, and `RFID_DEVICE_TOKEN`. Keep `RFID_CLASS_NAME` empty to accept cards from every class.
4. Find the computer's LAN IPv4 address with `ipconfig`. Replace `192.168.1.50` in both `RFID_ENDPOINT` and `RFID_STATUS_ENDPOINT` with that address. Keep port `5000` and the existing endpoint paths.
5. Set a long random `RFID_DEVICE_TOKEN` in `.env` and use the same token in the firmware. Leave `RFID_DEVICE_CLASS` blank for multi-class operation; each card scan is matched to its registered student and returns that student's own class. The idle status poll then returns school-wide totals without exposing a list of absent names. To restrict a reader to one class and show that class's absent names, set the same exact class in both `RFID_DEVICE_CLASS` and `RFID_CLASS_NAME`. The token authenticates both card scans and status polls. Do not commit or share the real token. Use the plain HTTP endpoints only on a trusted school LAN; production use should be HTTPS.
6. Keep the ESP32 and Flask server on the same trusted Wi-Fi/LAN. Start the app with `python run.py`; the current launcher listens on port 5000. Allow private-network access through the computer firewall if prompted.
7. Select the matching ESP32 board and port, upload, and open Serial Monitor at `115200` baud.

Do not use `localhost` or `127.0.0.1` in `RFID_ENDPOINT`: those addresses would point to the ESP32 itself. This sketch uses plain HTTP for a trusted local network. Do not expose port 5000 to the public internet; production deployment should use HTTPS and device authentication.

## LED and LCD feedback

- **Green:** a student was marked Present or a meeting check-in succeeded; the LCD shows the name and result.
- **Yellow:** student was marked Late, or an unlinked card was queued for office registration.
- **Red:** LCD is showing an Absent student, or a scan was denied/outside the attendance window, Wi-Fi/server failed, or card is not allowed.
- **No LED:** ready to scan.

An unknown card must first be linked to a student by authorized school staff in the web app. The server response and UID are also printed to Serial Monitor for troubleshooting.

If scans show the wrong status, open Serial Monitor at `115200` and compare `server_time` with `late_after` in the JSON response. Those values come from the Flask server clock and saved web settings. Confirm that Windows time/timezone is correct and that the allowed-time end is set correctly. After changing the allowed-time end, scanning again recalculates today's RFID record from its original arrival timestamp.

## Automatic absence recording and alerts

The ESP32 polls the protected attendance-status endpoint immediately after Wi-Fi connects and every 30 seconds, which saves absences after the 15-minute grace even when nobody scans or opens the attendance page. Open Serial Monitor at `115200` baud. A line such as `Attendance scope All classes updated at ...` confirms school-wide mode is working; it also reports how many new absences were saved. With a single-class restriction, the configured class appears instead. If the LCD shows `Class sync issue`, check the Serial Monitor for the cause. A fallback for when the device is offline is to schedule the Flask command below to run every minute on the server (Windows Task Scheduler or the production scheduler):

```powershell
py -m flask --app run.py reconcile-daily-attendance
```

The command is idempotent for the day. It stores an Absent record after the 15-minute grace period following the configured allowed-time end and sends in-app alerts to the affected student, linked guardians, class teachers, and authorized school staff. Late RFID scans queue alerts immediately. View alerts under **Notifications** in the web app. Parent email/SMS alerts are sent only when SMTP/Twilio providers are configured. Individual student attendance is not broadcast to unrelated families.

On Windows, create a Task Scheduler task that runs `reconcile-attendance-local.cmd` every minute if the local SQLite database is selected with `run-local.cmd`. If using PostgreSQL, schedule `py -m flask --app run.py reconcile-daily-attendance` from the project directory so it uses the PostgreSQL URL in `.env`.

The LCD percentages use the registered roster as the denominator: the selected class when restricted, or the whole school when `RFID_DEVICE_CLASS` is blank. Before the end of the 15-minute grace period, students with no status are shown in the unmarked count; after reconciliation they are counted as absent. Single-class mode rotates through that class's absent names and percentages. School-wide mode alternates the aggregate percentages with an `ABSENT` student count, without exposing individual names on the device; individual attendance remains visible only to authorized users in the school system.
