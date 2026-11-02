import serial
from pynput import mouse

ARDUINO_PORT = "COM5"   # check Device Manager -> Ports for the real one
BAUD = 9600

ser = serial.Serial(ARDUINO_PORT, BAUD, timeout=1)

def on_click(x, y, button, pressed):
    if button == mouse.Button.right:
        ser.write(b'S' if pressed else b'E')

with mouse.Listener(on_click=on_click) as listener:
    listener.join()
