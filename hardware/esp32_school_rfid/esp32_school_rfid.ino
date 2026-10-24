#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <LiquidCrystal_I2C.h>
#include <MFRC522.h>
#include <SPI.h>
#include <WiFi.h>
#include <Wire.h>

// Update these values for your Wi-Fi and the computer running the Flask app.
const char *WIFI_SSID = "IAP4";
const char *WIFI_PASSWORD = "123@Muhura";
const char *RFID_ENDPOINT = "http://192.168.1.50:5000/student/rfid-login";
const char *RFID_STATUS_ENDPOINT = "http://192.168.1.50:5000/student/rfid-status";
const char *RFID_DEVICE_TOKEN="rfid-esp32-7c9f3a2d8b614e05a6c1f928d47b3a50";
// Keep this empty to scan students from any class. Each scan uses the class
// linked to that card in the school system.
const char *RFID_CLASS_NAME = "";


// ESP32 DevKit + RC522 SPI reader.
constexpr uint8_t RFID_SS_PIN = 5;
constexpr uint8_t RFID_RST_PIN = 4;
constexpr uint8_t RFID_SCK_PIN = 18;
constexpr uint8_t RFID_MISO_PIN = 19;
constexpr uint8_t RFID_MOSI_PIN = 23;

// I2C LCD. Change LCD_ADDRESS to 0x3F if that is your backpack's address.
constexpr uint8_t LCD_SDA_PIN = 21;
constexpr uint8_t LCD_SCL_PIN = 22;
constexpr uint8_t LCD_ADDRESS = 0x27;

// Each LED needs its own 220-330 ohm series resistor.
constexpr uint8_t LED_RED_PIN = 25;
constexpr uint8_t LED_YELLOW_PIN = 26;
constexpr uint8_t LED_GREEN_PIN = 27;

constexpr unsigned long CARD_REPEAT_GUARD_MS = 10000;
constexpr unsigned long WIFI_RETRY_INTERVAL_MS = 10000;
constexpr unsigned long RFID_STATUS_POLL_MS = 30000;
constexpr unsigned long LCD_IDLE_ROTATION_MS = 2800;
constexpr uint8_t MAX_ABSENT_STUDENTS = 40;

MFRC522 rfid(RFID_SS_PIN, RFID_RST_PIN);
LiquidCrystal_I2C lcd(LCD_ADDRESS, 16, 2);

String lastUid;
unsigned long lastUidReadAt = 0;
unsigned long lastWifiRetryAt = 0;
unsigned long lastStatusPollAt = 0;
unsigned long lastIdleRenderAt = 0;
unsigned long idleScreenNumber = 0;
String absentStudents[MAX_ABSENT_STUDENTS];
uint8_t absentStudentCount = 0;
int statusAbsentCount = 0;
String statusClassName;
String statusSummaryLine = "Attendance pending";
String statusPollError;

void setLeds(bool red, bool yellow, bool green) {
  digitalWrite(LED_RED_PIN, red ? HIGH : LOW);
  digitalWrite(LED_YELLOW_PIN, yellow ? HIGH : LOW);
  digitalWrite(LED_GREEN_PIN, green ? HIGH : LOW);
}

void showLine(uint8_t row, String value) {
  if (value.length() > 16) {
    value = value.substring(0, 16);
  }
  while (value.length() < 16) {
    value += ' ';
  }
  lcd.setCursor(0, row);
  lcd.print(value);
}

void showMessage(const String &line1, const String &line2) {
  showLine(0, line1);
  showLine(1, line2);
}

String urlEncode(const String &value) {
  const char hex[] = "0123456789ABCDEF";
  String encoded;
  for (size_t i = 0; i < value.length(); i++) {
    const uint8_t c = static_cast<uint8_t>(value[i]);
    if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
        (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~') {
      encoded += static_cast<char>(c);
    } else {
      encoded += '%';
      encoded += hex[c >> 4];
      encoded += hex[c & 0x0F];
    }
  }
  return encoded;
}

bool refreshClassStatus() {
  if (WiFi.status() != WL_CONNECTED) {
    statusPollError = "Wi-Fi offline";
    Serial.println("Attendance status poll skipped: Wi-Fi is offline.");
    return false;
  }
  if (String(RFID_DEVICE_TOKEN).length() == 0 ||
      String(RFID_DEVICE_TOKEN).startsWith("SET_THE_SAME")) {
    statusPollError = "Set device token";
    Serial.println("Attendance status poll disabled: set RFID_DEVICE_TOKEN in the sketch and .env.");
    return false;
  }
  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(3000);
  http.setTimeout(5000);
  String url = RFID_STATUS_ENDPOINT;
  if (String(RFID_CLASS_NAME).length() > 0) {
    url += "?class=" + urlEncode(RFID_CLASS_NAME);
  }
  if (!http.begin(client, url)) {
    statusPollError = "Check server IP";
    Serial.println("Attendance status poll failed: check RFID_STATUS_ENDPOINT and server IP.");
    return false;
  }
  http.addHeader("X-Device-Token", RFID_DEVICE_TOKEN);
  const int httpCode = http.GET();
  if (httpCode != HTTP_CODE_OK) {
    const String errorBody = http.getString();
    statusPollError = httpCode == HTTP_CODE_UNAUTHORIZED ? "Bad device token" :
                      httpCode == HTTP_CODE_FORBIDDEN ? "Class not allowed" :
                      httpCode == HTTP_CODE_SERVICE_UNAVAILABLE ? "Server config" :
                      "Check server/API";
    Serial.printf("Class status request failed: HTTP %d: %s\n",
                  httpCode, errorBody.c_str());
    http.end();
    return false;
  }

  DynamicJsonDocument statusDocument(4096);
  const DeserializationError parseError = deserializeJson(statusDocument, http.getString());
  http.end();
  if (parseError || String(statusDocument["status"] | "") != "success") {
    statusPollError = "Bad server reply";
    Serial.printf("Could not parse class status response: %s\n", parseError.c_str());
    return false;
  }

  statusPollError = "";
  statusClassName = statusDocument["class_name"] | "All classes";
  JsonObject summary = statusDocument["class_summary"].as<JsonObject>();
  const int presentPercent = summary["present_percent"] | 0;
  const int latePercent = summary["late_percent"] | 0;
  const int absentPercent = summary["absent_percent"] | 0;
  statusAbsentCount = summary["absent_count"] | 0;
  statusSummaryLine = String("P") + presentPercent + "%L" + latePercent + "%A" + absentPercent + "%";

  absentStudentCount = 0;
  JsonArray names = statusDocument["absent_students"].as<JsonArray>();
  for (JsonVariant name : names) {
    if (absentStudentCount >= MAX_ABSENT_STUDENTS) {
      break;
    }
    absentStudents[absentStudentCount++] = name.as<String>();
  }
  idleScreenNumber = 0;
  lastIdleRenderAt = 0;
  const String updatedAt = statusDocument["updated_at"] | "time unavailable";
  const int absencesRecorded = statusDocument["absences_recorded"] | 0;
  Serial.printf("Attendance scope %s updated at %s: %u listed absent student(s); %s; %d new absence(s) saved\n",
                statusClassName.c_str(), updatedAt.c_str(), absentStudentCount,
                statusSummaryLine.c_str(), absencesRecorded);
  if (absencesRecorded > 0) {
    Serial.println("The server saved the absences and queued in-app attendance alerts.");
  }
  return true;
}

void renderIdleClassStatus() {
  if (statusClassName.length() == 0) {
    if (statusPollError.length() > 0) {
      showMessage("Class sync issue", statusPollError);
    } else {
      showMessage("School RFID", "Tap student card");
    }
    return;
  }
  if (millis() - lastIdleRenderAt < LCD_IDLE_ROTATION_MS) {
    return;
  }
  lastIdleRenderAt = millis();

  if (absentStudentCount == 0) {
    if (statusAbsentCount == 0) {
      showMessage(statusClassName, statusSummaryLine);
      setLeds(false, false, false);
    } else if (idleScreenNumber % 2 == 0) {
      showMessage(statusClassName, statusSummaryLine);
      setLeds(false, false, false);
    } else {
      showMessage("ABSENT", String("Students: ") + statusAbsentCount);
      setLeds(true, false, false);
    }
    idleScreenNumber++;
    return;
  }

  const uint8_t slot = idleScreenNumber % 6;
  if (slot == 0) {
    showMessage(statusClassName, statusSummaryLine);
    setLeds(false, false, false);
  } else {
    const uint8_t groupOffset = ((idleScreenNumber / 6) * 5) % absentStudentCount;
    const uint8_t studentIndex = (groupOffset + slot - 1) % absentStudentCount;
    showMessage(absentStudents[studentIndex], "ABSENT");
    setLeds(true, false, false);
  }
  idleScreenNumber++;
}

String readUid() {
  String uid;
  uid.reserve(rfid.uid.size * 2);
  for (byte i = 0; i < rfid.uid.size; i++) {
    if (rfid.uid.uidByte[i] < 0x10) {
      uid += '0';
    }
    uid += String(rfid.uid.uidByte[i], HEX);
  }
  uid.toUpperCase();
  return uid;
}

void beginWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  showMessage("Connecting WiFi", WIFI_SSID);
  Serial.print("Connecting to Wi-Fi");
  const unsigned long startedAt = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startedAt < 20000) {
    delay(500);
    Serial.print('.');
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("Wi-Fi IP: ");
    Serial.println(WiFi.localIP());
    showMessage("WiFi connected", WiFi.localIP().toString());
    setLeds(false, false, true);
    delay(1500);
    setLeds(false, false, false);
    showMessage("School RFID", "Tap student card");
  } else {
    Serial.println("Wi-Fi connection failed. Will retry in the background.");
    setLeds(true, false, false);
    showMessage("WiFi unavailable", "Check settings");
  }
}

void keepWifiConnected() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  if (millis() - lastWifiRetryAt >= WIFI_RETRY_INTERVAL_MS) {
    lastWifiRetryAt = millis();
    Serial.println("Retrying Wi-Fi...");
    WiFi.reconnect();
  }
}

int postRfidUid(const String &uid, String &responseBody) {
  if (WiFi.status() != WL_CONNECTED) {
    return -1;
  }

  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(5000);
  http.setTimeout(8000);

  if (!http.begin(client, RFID_ENDPOINT)) {
    Serial.println("Could not start HTTP request. Check RFID_ENDPOINT.");
    return -1;
  }

  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Token", RFID_DEVICE_TOKEN);
  StaticJsonDocument<96> requestDocument;
  requestDocument["uid"] = uid;
  String payload;
  serializeJson(requestDocument, payload);
  Serial.printf("[RFID] POST %s\n", RFID_ENDPOINT);
  Serial.printf("[RFID] Request JSON: %s\n", payload.c_str());

  const int httpCode = http.POST(payload);
  if (httpCode > 0) {
    responseBody = http.getString();
  } else {
    Serial.printf("HTTP request failed: %s\n", http.errorToString(httpCode).c_str());
  }
  http.end();
  return httpCode;
}

void showServerResult(int httpCode, const String &responseBody) {
  Serial.printf("[RFID] HTTP %d response:\n%s\n", httpCode, responseBody.c_str());

  if (httpCode <= 0) {
    setLeds(true, false, false);
    showMessage("Server error", "Check WiFi/IP");
    return;
  }

  DynamicJsonDocument result(1536);
  const DeserializationError parseError = deserializeJson(result, responseBody);
  if (parseError) {
    setLeds(true, false, false);
    showMessage("Bad server reply", "See Serial log");
    return;
  }

  const String status = result["status"] | "unknown";
  const String student = result["student"] | "";
  const String attendance = result["attendance_status"] | "";
  String attendanceLabel = attendance;
  attendanceLabel.toUpperCase();
  const String timestamp = result["timestamp"] | "";
  const String message = result["message"] | "Request failed";

  if (status == "device_unauthorized") {
    setLeds(true, false, false);
    showMessage("Device not auth", "Check token");
    Serial.println("RFID scan rejected: configure the same RFID_DEVICE_TOKEN on the server and ESP32.");
    return;
  }

  if (status == "student_inactive") {
    setLeds(true, false, false);
    showMessage(student.length() > 0 ? student : "Student account", "INACTIVE: OFFICE");
    Serial.println("RFID scan denied: this card belongs to an archived student account.");
    return;
  }

  if (status == "class_not_allowed") {
    setLeds(true, false, false);
    showMessage("Class not allowed", "Check reader setup");
    Serial.println("RFID scan rejected: student's class is outside RFID_DEVICE_CLASS on the server.");
    return;
  }

  if (status == "success" || status == "meeting_checkin") {
    if (attendance == "Present") {
      setLeds(false, false, true);
    } else if (attendance == "Late") {
      setLeds(false, true, false);
    } else if (attendance == "Absent") {
      setLeds(true, false, false);
    } else {
      setLeds(false, false, true);
    }
    if (student.length() > 0 && attendance.length() > 0) {
      showMessage(student, attendanceLabel);
      delay(2200);
    } else if (student.length() > 0) {
      showMessage("Meeting check-in", student);
      delay(2200);
    } else {
      showMessage("Scan accepted", message);
      delay(2200);
    }

    if (timestamp.length() >= 19) {
      showMessage(String("Date ") + timestamp.substring(0, 10),
                  String("Time ") + timestamp.substring(11, 19));
      delay(1600);
    }

    const String className = result["class_name"] | "Unassigned";
    JsonObject summary = result["class_summary"].as<JsonObject>();
    if (!summary.isNull()) {
      const int presentPercent = summary["present_percent"] | 0;
      const int latePercent = summary["late_percent"] | 0;
      const int absentPercent = summary["absent_percent"] | 0;
      const int unmarkedCount = summary["unmarked_count"] | 0;

      showMessage(className,
                  String("P") + presentPercent + "%L" + latePercent + "%A" + absentPercent + "%");
      delay(2800);

      if (unmarkedCount > 0) {
        showMessage("Not marked yet:", String("Students: ") + unmarkedCount);
        delay(1800);
      }
    }
    return;
  }

  if (status == "pending_registration") {
    setLeds(false, true, false);
    showMessage("Card not linked", "Queued for office");
    return;
  }

  setLeds(true, false, false);
  if (status == "access_denied") {
    showMessage(student.length() > 0 ? student : "ACCESS DENIED", "ACCESS DENIED");
    delay(2200);
  } else if (status == "outside_window") {
    showMessage("Outside scan", "time window");
    delay(1800);
    const String className = result["class_name"] | "Unassigned";
    JsonObject summary = result["class_summary"].as<JsonObject>();
    if (!summary.isNull()) {
      const int presentPercent = summary["present_percent"] | 0;
      const int latePercent = summary["late_percent"] | 0;
      const int absentPercent = summary["absent_percent"] | 0;
      showMessage(className,
                  String("P") + presentPercent + "%L" + latePercent + "%A" + absentPercent + "%");
      delay(2800);
    }
  } else if (status == "lost_card" || status == "card_not_eligible") {
    showMessage("Card not allowed", "Contact office");
  } else {
    showMessage("Scan rejected", message);
  }
}

void setup() {
  Serial.begin(115200);

  pinMode(LED_RED_PIN, OUTPUT);
  pinMode(LED_YELLOW_PIN, OUTPUT);
  pinMode(LED_GREEN_PIN, OUTPUT);
  setLeds(false, false, false);

  Wire.begin(LCD_SDA_PIN, LCD_SCL_PIN);
  lcd.init();
  lcd.backlight();
  showMessage("School RFID", "Starting...");

  SPI.begin(RFID_SCK_PIN, RFID_MISO_PIN, RFID_MOSI_PIN, RFID_SS_PIN);
  rfid.PCD_Init();
  delay(50);

  beginWifi();
  lastStatusPollAt = millis() - RFID_STATUS_POLL_MS;
}

void loop() {
  keepWifiConnected();

  if (millis() - lastStatusPollAt >= RFID_STATUS_POLL_MS) {
    lastStatusPollAt = millis();
    refreshClassStatus();
  }

  renderIdleClassStatus();

  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) {
    delay(20);
    return;
  }

  const String uid = readUid();
  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();

  if (uid == lastUid && millis() - lastUidReadAt < CARD_REPEAT_GUARD_MS) {
    delay(50);
    return;
  }
  lastUid = uid;
  lastUidReadAt = millis();

  Serial.printf("Card read: %s\n", uid.c_str());
  if (WiFi.status() != WL_CONNECTED) {
    setLeds(true, false, false);
    showMessage("Card read", "WiFi offline");
    delay(1800);
    lastUidReadAt = millis();
    showMessage("School RFID", "Tap student card");
    return;
  }

  setLeds(false, true, false);
  showMessage("Sending card...", uid);
  String responseBody;
  const int httpCode = postRfidUid(uid, responseBody);
  showServerResult(httpCode, responseBody);
  // Start the repeat guard after the response has finished displaying so a held
  // or immediately re-tapped card cannot flood the Flask endpoint.
  lastUidReadAt = millis();

  setLeds(false, false, false);
  lastIdleRenderAt = 0;
}
