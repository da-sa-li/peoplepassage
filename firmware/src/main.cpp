// PeoplePassage – ESP32 + VL53L1X Türsensor
//
// Erkennt gerichtete Durchgänge (rein/raus) mit EINEM VL53L1X per Dual-ROI:
// das Sichtfeld wird in zwei nebeneinander liegende Zonen (links/rechts) quer
// über die Türschwelle geteilt. Die Reihenfolge, in der eine Person die Zonen
// auslöst, ergibt die Richtung (ST-„People-Counting"-Technik).
//
// Meldet per WLAN/MQTT an den zentralen Server:
//   peoplepassage/<id>/event   {seq, direction:"in"|"out"}
//   peoplepassage/<id>/status  {online, rssi, baseline_mm, fw, uptime}  (retained, LWT)
// und reagiert auf:
//   peoplepassage/<id>/cmd     {cmd:"calibrate"|"reboot"}

#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <Preferences.h>
#include <SparkFun_VL53L1X.h>

#include "config.h"

// ----- Topics -----
static const String T_EVENT  = String("peoplepassage/") + SENSOR_ID + "/event";
static const String T_STATUS = String("peoplepassage/") + SENSOR_ID + "/status";
static const String T_CMD    = String("peoplepassage/") + SENSOR_ID + "/cmd";

// ----- Globals -----
SFEVL53L1X sensor;
WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);
Preferences prefs;

static const uint16_t ROI_CENTERS[2] = {ROI_CENTER_LEFT, ROI_CENTER_RIGHT};
static uint8_t  g_zone = 0;                 // aktuell gemessene Zone (0=links,1=rechts)
static uint16_t g_baseline_mm = DEFAULT_BASELINE_MM;
static uint16_t g_threshold_mm = DEFAULT_BASELINE_MM - PRESENCE_MARGIN_MM;
static uint32_t g_seq = 0;                  // monoton, in NVS persistiert
static uint32_t g_last_status = 0;

// People-Counting-Zustände
#define NOBODY  0
#define SOMEONE 1
#define LEFT    0
#define RIGHT   1

// --------------------------------------------------------------------------
// Persistenz der Sequenznummer (verhindert Duplikat-Kollisionen nach Reboot,
// da der Server per (sensor_id, seq) dedupliziert).
// --------------------------------------------------------------------------
static void loadState() {
  prefs.begin("pp", false);
  g_seq = prefs.getULong("seq", 0);
  g_baseline_mm = prefs.getUShort("baseline", DEFAULT_BASELINE_MM);
  prefs.end();
  g_threshold_mm = (g_baseline_mm > PRESENCE_MARGIN_MM)
                       ? (g_baseline_mm - PRESENCE_MARGIN_MM)
                       : g_baseline_mm;
}

static void saveSeq() {
  prefs.begin("pp", false);
  prefs.putULong("seq", g_seq);
  prefs.end();
}

static void saveBaseline() {
  prefs.begin("pp", false);
  prefs.putUShort("baseline", g_baseline_mm);
  prefs.end();
}

// --------------------------------------------------------------------------
// VL53L1X
// --------------------------------------------------------------------------
static void applyRoi(uint8_t zone) {
  sensor.setROI(ROI_WIDTH, ROI_HEIGHT, ROI_CENTERS[zone]);
}

// Eine Distanzmessung der Zone `zone` (mm). 0 = ungültig.
static uint16_t measureZone(uint8_t zone) {
  applyRoi(zone);
  sensor.startRanging();
  uint32_t t0 = millis();
  while (!sensor.checkForDataReady()) {
    if (millis() - t0 > 100) {  // Timeout
      sensor.stopRanging();
      return 0;
    }
    delay(1);
  }
  uint16_t d = sensor.getDistance();
  sensor.clearInterrupt();
  sensor.stopRanging();
  return d;
}

// --------------------------------------------------------------------------
// People-Counting-Zustandsmaschine (adaptiert von STs Beispiel).
// Rückgabe: 0 = kein Ereignis, 1 = "in", 2 = "out".
// --------------------------------------------------------------------------
static int processCounting(int16_t distance, uint8_t zone) {
  static int pathTrack[4] = {0, 0, 0, 0};
  static int pathFill = 1;
  static int leftPrev = NOBODY;
  static int rightPrev = NOBODY;

  int current = NOBODY;
  int zonesStatus = 0;
  int eventOccured = 0;
  int direction = 0;

  if (distance > MIN_VALID_MM && distance < (int16_t)g_threshold_mm) {
    current = SOMEONE;
  }

  if (zone == LEFT) {
    if (current != leftPrev) {
      eventOccured = 1;
      if (current == SOMEONE) zonesStatus += 1;
      if (rightPrev == SOMEONE) zonesStatus += 2;
      leftPrev = current;
    }
  } else {  // RIGHT
    if (current != rightPrev) {
      eventOccured = 1;
      if (current == SOMEONE) zonesStatus += 2;
      if (leftPrev == SOMEONE) zonesStatus += 1;
      rightPrev = current;
    }
  }

  if (eventOccured) {
    if (pathFill < 4) pathFill++;
    if (leftPrev == NOBODY && rightPrev == NOBODY) {
      // Niemand mehr im Sichtfeld -> Pfad auswerten
      if (pathFill == 4) {
        if (pathTrack[1] == 1 && pathTrack[2] == 3 && pathTrack[3] == 2) {
          direction = 1;  // links -> rechts = rein
        } else if (pathTrack[1] == 2 && pathTrack[2] == 3 && pathTrack[3] == 1) {
          direction = 2;  // rechts -> links = raus
        }
      }
      pathFill = 1;
    } else {
      if (pathFill - 1 < 4) pathTrack[pathFill - 1] = zonesStatus;
    }
  }
  return direction;
}

// --------------------------------------------------------------------------
// MQTT
// --------------------------------------------------------------------------
static void publishStatus(bool online) {
  char buf[200];
  snprintf(buf, sizeof(buf),
           "{\"online\":%s,\"rssi\":%d,\"baseline_mm\":%u,\"fw\":\"%s\",\"uptime\":%lu}",
           online ? "true" : "false", (int)WiFi.RSSI(), g_baseline_mm,
           FIRMWARE_VERSION, (unsigned long)(millis() / 1000));
  mqtt.publish(T_STATUS.c_str(), buf, true);  // retained
}

static void publishEvent(int direction) {
  g_seq++;
  saveSeq();
  char buf[64];
  snprintf(buf, sizeof(buf), "{\"seq\":%lu,\"direction\":\"%s\"}",
           (unsigned long)g_seq, direction == 1 ? "in" : "out");
  mqtt.publish(T_EVENT.c_str(), buf, false);
  Serial.printf("event #%lu %s\n", (unsigned long)g_seq, direction == 1 ? "in" : "out");
}

// Baseline neu vermessen (nur ausführen, wenn niemand unter dem Sensor steht).
static void calibrate() {
  Serial.println("Kalibriere Baseline ...");
  uint32_t sum = 0;
  uint16_t n = 0;
  for (uint16_t i = 0; i < CALIB_SAMPLES; i++) {
    uint16_t d = measureZone(i & 1);  // beide Zonen abwechselnd
    if (d > MIN_VALID_MM) { sum += d; n++; }
    delay(5);
  }
  if (n > 0) {
    g_baseline_mm = (uint16_t)(sum / n);
    g_threshold_mm = (g_baseline_mm > PRESENCE_MARGIN_MM)
                         ? (g_baseline_mm - PRESENCE_MARGIN_MM)
                         : g_baseline_mm;
    saveBaseline();
    Serial.printf("Baseline=%u mm, Schwelle=%u mm\n", g_baseline_mm, g_threshold_mm);
  } else {
    Serial.println("Kalibrierung fehlgeschlagen (keine gültigen Messungen).");
  }
  publishStatus(true);
}

static void onMqttMessage(char* topic, byte* payload, unsigned int len) {
  String msg;
  for (unsigned int i = 0; i < len; i++) msg += (char)payload[i];
  if (msg.indexOf("calibrate") >= 0) {
    calibrate();
  } else if (msg.indexOf("reboot") >= 0) {
    publishStatus(false);
    delay(100);
    ESP.restart();
  }
}

static void ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("WLAN verbinde");
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
    delay(300);
    Serial.print(".");
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? " ok" : " timeout");
}

static void ensureMqtt() {
  if (mqtt.connected()) return;
  ensureWifi();
  if (WiFi.status() != WL_CONNECTED) return;
  // LWT: markiert den Sensor offline, falls die Verbindung unerwartet abreißt.
  const char* willMsg = "{\"online\":false}";
  if (mqtt.connect(SENSOR_ID, MQTT_USER, MQTT_PASS, T_STATUS.c_str(), 1, true, willMsg)) {
    Serial.println("MQTT verbunden");
    mqtt.subscribe(T_CMD.c_str(), 1);
    publishStatus(true);
  } else {
    Serial.printf("MQTT-Fehler rc=%d\n", mqtt.state());
    delay(2000);
  }
}

// --------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(200);
  loadState();

  Wire.begin(PIN_SDA, PIN_SCL);
  if (sensor.begin() != 0) {  // 0 = ok
    Serial.println("VL53L1X nicht gefunden! Verkabelung prüfen.");
    while (true) delay(1000);
  }
  sensor.setDistanceModeLong();
  sensor.setTimingBudgetInMs(TIMING_BUDGET_MS);

  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);
  mqtt.setKeepAlive(30);

  ensureMqtt();
  Serial.printf("Bereit. id=%s baseline=%u schwelle=%u\n",
                SENSOR_ID, g_baseline_mm, g_threshold_mm);
}

void loop() {
  ensureMqtt();
  mqtt.loop();

  // Eine Zone messen, Zustandsmaschine füttern, ggf. Event senden.
  uint16_t d = measureZone(g_zone);
  int dir = processCounting((int16_t)d, g_zone);
  if (dir != 0 && mqtt.connected()) {
    publishEvent(dir);
  }
  g_zone ^= 1;  // Zone für nächste Messung wechseln

  // Heartbeat alle 15 s
  if (millis() - g_last_status > 15000) {
    g_last_status = millis();
    if (mqtt.connected()) publishStatus(true);
  }
}
