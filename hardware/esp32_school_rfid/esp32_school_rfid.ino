#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <LiquidCrystal_I2C.h>
#include <MFRC522.h>
#include <SPI.h>
#include <WiFi.h>
#include <Wire.h>

// Update these values for your Wi-Fi and the computer running the Flask app.
const char *WIFI_SSID = "YOUR_WIFI_NAME";
const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char *RFID_ENDPOINT = "http://192.168.1.50:5000/student/rfid-login";

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

constexpr unsigned long CARD_REPEAT_GUARD_MS = 4000;
constexpr unsigned long WIFI_RETRY_INTERVAL_MS = 10000;

MFRC522 rfid(RFID_SS_PIN, RFID_RST_PIN);
LiquidCrystal_I2C lcd(LCD_ADDRESS, 16, 2);

String lastUid;
unsigned long lastUidReadAt = 0;
unsigned long lastWifiRetryAt = 0;

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
  const String payload = String("{\"uid\":\"") + uid + "\"}";
  Serial.printf("POST %s  UID=%s\n", RFID_ENDPOINT, uid.c_str());

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
  Serial.printf("HTTP %d: %s\n", httpCode, responseBody.c_str());

  if (httpCode <= 0) {
    setLeds(true, false, false);
    showMessage("Server error", "Check WiFi/IP");
    return;
  }

  DynamicJsonDocument result(1024);
  const DeserializationError parseError = deserializeJson(result, responseBody);
  if (parseError) {
    setLeds(true, false, false);
    showMessage("Bad server reply", "See Serial log");
    return;
  }

  const String status = result["status"] | "unknown";
  const String student = result["student"] | "";
  const String attendance = result["attendance_status"] | "";
  const String message = result["message"] | "Request failed";

  if (status == "success" || status == "meeting_checkin") {
    setLeds(false, false, true);
    if (student.length() > 0 && attendance.length() > 0) {
      showMessage(student, attendance);
    } else if (student.length() > 0) {
      showMessage("Meeting check-in", student);
    } else {
      showMessage("Scan accepted", message);
    }
    return;
  }

  if (status == "pending_registration") {
    setLeds(false, true, false);
    showMessage("Card not linked", "Queued for office");
    return;
  }

  setLeds(true, false, false);
  if (status == "outside_window") {
    showMessage("Outside scan", "time window");
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
}

void loop() {
  keepWifiConnected();

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
    showMessage("School RFID", "Tap student card");
    return;
  }

  setLeds(false, true, false);
  showMessage("Sending card...", uid);
  String responseBody;
  const int httpCode = postRfidUid(uid, responseBody);
  showServerResult(httpCode, responseBody);
  delay(2200);

  setLeds(false, false, false);
  showMessage("School RFID", "Tap student card");
}
