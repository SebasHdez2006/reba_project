#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
serial_reba.py — Monitor serie + aprovisionamiento de la ESP32-CAM REBA.

Uso:
  serial_reba.py                    -> detecta puerto, escucha 30 s
  serial_reba.py --listen 60        -> escucha N segundos
  serial_reba.py --send GET STATUS  -> envia comandos y muestra respuestas
  serial_reba.py --send "SET wifi_ssid=REBA-AP" "SET wifi_pass=clave"
                                    "SET server_url=http://192.168.137.1:5000"
  serial_reba.py --port COM10 --send GET

Comandos del firmware (HELP en serie):
  GET | STATUS | SET clave=valor | CAPTURE | OTA [url] | REBOOT
"""
import sys, time, argparse

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("Falta pyserial: .venv-reba\\Scripts\\python.exe -m pip install pyserial")

BAUD = 115200
CHIP_HINTS = ("ch340", "ch341", "ch9102", "cp210", "ftdi", "usb-serial", "usb serial")


def detect_port(preferido=None):
    if preferido:
        return preferido
    ports = list(list_ports.comports())
    if not ports:
        sys.exit("No hay puertos COM. Conecta la ESP32 por USB (cable de datos, no solo carga).")
    for p in ports:
        desc = (p.description or "") + " " + (p.device or "")
        if any(h in desc.lower() for h in CHIP_HINTS):
            return p.device
    if len(ports) == 1:
        return ports[0].device
    print("Puertos disponibles:", file=sys.stderr)
    for p in ports:
        print("  %s  %s" % (p.device, p.description), file=sys.stderr)
    sys.exit("Varios puertos: especifica con --port COMx")


def listen(ser, segundos, sent_cmd=None):
    deadline = time.time() + segundos
    buf = b""
    while time.time() < deadline:
        n = ser.in_waiting
        if n:
            buf += ser.read(n)
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                texto = line.decode("utf-8", "replace").rstrip("\r")
                print(texto, flush=True)
        else:
            if sent_cmd and not n:
                pass
            time.sleep(0.05)
    if buf:
        print(buf.decode("utf-8", "replace"), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", help="COMx (autodetecta CH340 si se omite)")
    ap.add_argument("--listen", type=float, default=None, help="segundos a escuchar (default 30)")
    ap.add_argument("--send", nargs="+", default=None,
                    help="comandos a enviar, c/u entre comillas")
    args = ap.parse_args()

    port = detect_port(args.port)
    print("== Puerto: %s @ %d ==" % (port, BAUD), flush=True)
    try:
        ser = serial.Serial(port, BAUD, timeout=0.1)
    except serial.SerialException as e:
        sys.exit("No se pudo abrir %s: %s" % (port, e))

    # DTR/RTS bajos para no resetear la placa al abrir
    try:
        ser.setDTR(False); ser.setRTS(False)
    except Exception:
        pass
    time.sleep(0.3)
    ser.reset_input_buffer()

    if args.send:
        for cmd in args.send:
            ser.write((cmd + "\r\n").encode("utf-8"))
            print(">> %s" % cmd, flush=True)
            listen(ser, 2.0, sent_cmd=cmd)
        listen(ser, args.listen if args.listen else 10.0)
    else:
        listen(ser, args.listen if args.listen else 30.0)

    ser.close()


if __name__ == "__main__":
    main()
