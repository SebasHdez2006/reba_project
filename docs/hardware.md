# REBA — Hardware y esquema eléctrico

Este documento describe el cableado exacto de la plataforma flotante: alimentación, bomba, relé (cableado en **NC**), ESP32-CAM, flash y sensor ultrasónico.

---

## Lista de materiales (BOM)

| Componente | Modelo / nota | Rol |
|------------|---------------|-----|
| Batería Li-ion 18650 | Steren 3.7 V, 2800 mAh | Energía principal del boost/bomba |
| Módulo de carga | TP4056 | Carga desde panel solar 5 V |
| Panel solar | 5 V (~100 mA) | Entrada a TP4056 (IN+ / IN−) |
| Boost step-up | XL6009E1 | Eleva ~3.7 V → **12 V** para la bomba |
| Switch 2 pines | — | Corta alimentación entre TP4056 OUT y XL6009 IN |
| Mini bomba sumergible | ARD-29209 / AD20P-1230C | 12 VDC, 350 mA, 3.6 W, IP68 |
| Relé 1 canal | 5 V bobina típica (pines DC+/DC−/IN y COM/NC/NO + jumper) | Conmuta **+12 V** de la bomba en **NC** |
| ESP32-CAM | AI-Thinker OV2640 | Cámara + Wi-Fi + GPIO + flash LED |
| Fuente 5 V ESP32 | Power bank / regulador dedicado | Pines **5V** y **GND** del ESP32 (vía protoboard) |
| Ultrasónico | HC-SR04 o JSN-SR04T | Detección de ingreso de objetos (0–23 cm) |
| Resistencias | 1 kΩ + 2 kΩ | Divisor ECHO 5 V → 3.3 V |

---

## Diagrama de bloques de alimentación

```
Panel solar 5V
      │
      ▼
  TP4056 (IN+/IN−)
      │ OUT+ / OUT−  (batería 18650 en B+/B−)
      ▼
  Switch 2 pines
      │
      ▼
  XL6009E1  ──►  +12 V  ──►  COM del RELÉ ──► NC ──►  + bomba
                 GND 12V ───────────────────────────────►  − bomba

Power bank / 5V regulado ──► protoboard (+/−) ──► ESP32-CAM (5V, GND)
                                                  y VCC relé / sensor
```

### Notas críticas de potencia

1. **Nunca** alimentes el ESP32-CAM desde la salida 12 V del XL6009.
2. El ESP32 usa fuente **5 V dedicada** en los pines `5V` y `GND` de la placa.
3. Ajusta el potenciómetro del XL6009 midiendo con multímetro hasta **12.0 V** antes de conectar la bomba.
4. Corriente de bomba ≈ 350 mA; el boost y el relé deben soportar al menos ese pico de arranque.

---

## Mapeo de pines ESP32-CAM (AI-Thinker)

| Función | GPIO | Dirección | Notas |
|---------|------|-----------|-------|
| Relé (bomba) | **13** | Salida | `RELE_ACTIVO_BAJO` (default: LOW = relé energizado) |
| Ultrasónico TRIG | **14** | Salida | Pulso 10 µs |
| Ultrasónico ECHO | **15** | Entrada | **Solo vía divisor 1k/2k** |
| Flash LED | **4** | Salida | LED blanco interno de la placa; se enciende ~0.3–0.4 s por foto |

Los pines de la cámara OV2640 están fijados en el sketch `fotos_nc.ino` según el pinout AI-Thinker estándar (D0–D7, XCLK, PCLK, VSYNC, HREF, SCCB, PWDN).

> GPIO4 (flash) es el LED blanco de la AI-Thinker: queda libre durante el resto del tiempo y solo se usa como flash momentáneo por foto.

---

## Cableado del relé y la bomba — **NC (normalmente cerrado)**

```
ESP32 GPIO13 ──────► IN  (módulo relé)
ESP32 GND     ──────► DC−/GND (módulo relé)
5V dedicado   ──────► DC+/VCC (módulo relé)   // según datasheet del módulo
                     (jumper JD-VCC/VCC según el módulo)

+12 V (XL6009) ──► COM del relé
NC del relé    ──► + de la bomba
GND 12 V       ──► − de la bomba
```

Con `BOMBA_EN_NC = true` (default del firmware desplegado):

| Estado del relé | Circuito COM↔NC | Bomba |
|-----------------|------------------|-------|
| **Energizado** (GPIO13 = LOW con `RELE_ACTIVO_BAJO`) | abierto | **OFF** |
| **En reposo** (GPIO13 = HIGH) | cerrado | **ON** |

- Esto significa que la bomba queda **encendida si el ESP32 pierde energía o el GPIO queda flotando**. El firmware llama `bombaApagar()` (relé energizado) como primera acción del `setup()` para partir de estado seguro.
- Si prefieres el comportamiento inverso (fail-safe OFF), recablea la bomba al pin **NO** del relé y pon `BOMBA_EN_NC false`.

---

## Sensor ultrasónico y divisor de voltaje ECHO

Muchos HC-SR04 alimentados a 5 V sacan **ECHO a 5 V**. El ESP32 solo tolera **3.3 V** en GPIO → usar divisor resistivo.

```
ECHO (5V) ───[ 1 kΩ ]───●─── GPIO15 (≈ 3.3 V)
                         │
                       [ 2 kΩ ]
                         │
                        GND
```

Cálculo:
\[
V_{GPIO} = 5 \times \frac{2}{1+2} \approx 3.33\,\text{V}
\]

| Señal | Conexión |
|-------|----------|
| VCC sensor | 5 V (misma referencia GND que ESP32) |
| GND sensor | GND común |
| TRIG | GPIO14 (directo; salida 3.3 V suele bastar) |
| ECHO | Divisor → GPIO15 |

### Montaje mecánico del sensor

- Orienta el sensor hacia la **zona de ingreso de la cesta** (donde entra la basura arrastrada por la corriente).
- Rango efectivo del firmware: **0–23 cm** (`DIST_MIN_CM`/`DIST_MAX_CM`). Un objeto dentro del rango dispara la foto; debe retirarse para contar el siguiente ingreso (ver `LECTURAS_PARA_CONTAR`/`LECTURAS_PARA_REARMAR`).
- Ajusta el rango según la geometría real de la cesta: si el fondo queda dentro de 23 cm el sensor contará "siempre un objeto".

---

## Tierra común

Todos los GND deben estar referenciados entre sí en lo posible:

- GND batería / TP4056 / XL6009 / bomba
- GND ESP32 (power bank, vía protoboard)
- GND módulo relé
- GND ultrasónico

Si el power bank del ESP32 está aislado, al menos une **GND ESP32 ↔ GND relé ↔ GND sensor** para que los niveles lógicos sean válidos. La potencia de la bomba puede compartir ese GND o unirse en un solo punto de estrella.

---

## Checklist de puesta en marcha hardware

1. [ ] Multímetro: salida XL6009 = 12 V ±0.3 V
2. [ ] Relé conmuta con GPIO13 (observa el ciclo de bomba tras 3 fotos o fuerza un ingreso con la mano a <23 cm)
3. [ ] Divisor ECHO instalado; GPIO15 nunca recibe 5 V directo
4. [ ] ESP32 estable con 5 V dedicado (sin resets al disparar flash/Wi-Fi)
5. [ ] Bomba cableada en **NC** (no en NO) y coherente con `BOMBA_EN_NC`
6. [ ] Sensor apuntando a la zona de ingreso; lecturas en Serial <23 cm solo cuando hay objeto

---

## Referencias de archivos

- Firmware: `esp32_firmware/fotos_nc/fotos_nc.ino`
- Parámetros servidor: `.env` / `server/config.py`
