#include <Mouse.h>

float xValue = 0;               // positive = right, negative = left; magnitude = speed (px/step)
float yValue = -5;              // positive = down, negative = up; magnitude = speed (px/step)
float smoothness = 0;           // 0 = perfectly straight; higher = more random wobble per step (e.g. 10 = very jittery); decimals allowed
const int stepDelayMs = 15;     // ms between steps
const unsigned long maxDurationMs = 10000; // safety cutoff in case a stop signal is missed

bool drawing = false;
unsigned long drawStart = 0;
float accumX = 0, accumY = 0;   // fractional carry so decimal speeds average out correctly

String inputBuffer = "";

void setup() {
  Serial.begin(9600);
  Mouse.begin();
  randomSeed(analogRead(A0));
}

// random float in [-smoothness, smoothness]; 0 when smoothness is 0
float jitter() {
  if (smoothness <= 0) return 0;
  return (random(-10000, 10001) / 10000.0) * smoothness;
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      handleCommand(inputBuffer);
      inputBuffer = "";
    } else if (c != '\r') {
      inputBuffer += c;
    }
  }

  if (drawing) {
    if (millis() - drawStart >= maxDurationMs) {
      drawing = false;
      Serial.println("DONE");
    } else {
      accumX += xValue + jitter();
      accumY += yValue + jitter(); // screen's "positive = down" already matches yValue's convention

      int dx = (int) round(accumX);
      int dy = (int) round(accumY);

      Mouse.move(dx, dy);

      accumX -= dx;
      accumY -= dy;

      delay(stepDelayMs);
    }
  }
}

void handleCommand(String cmd) {
  if (cmd.length() == 0) return;
  char code = cmd[0];
  String value = cmd.substring(1);

  if (code == 'S') {
    drawing = true;
    drawStart = millis();
    accumX = 0;
    accumY = 0;
    Serial.println("DRAWING");
  } else if (code == 'E') {
    drawing = false;
    Serial.println("STOPPED");
  } else if (code == 'X') {
    xValue = value.toFloat();
  } else if (code == 'Y') {
    yValue = value.toFloat();
  } else if (code == 'M') {
    smoothness = value.toFloat();
    if (smoothness < 0) smoothness = 0;
  }
}
