#pragma once

// Copy this file to rfid_config.h and enter the local device credentials.
// rfid_config.h is ignored by Git so Wi-Fi passwords and device tokens do not
// get committed with the firmware.
const char *WIFI_SSID = "YOUR_WIFI_NAME";
const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char *RFID_DEVICE_TOKEN = "SET_THE_SAME_LONG_RANDOM_TOKEN_IN_RENDER";

// Leave empty for cards from all registered classes and school-wide totals.
// Set only if this reader should be restricted to one exact class.
const char *RFID_CLASS_NAME = "";
