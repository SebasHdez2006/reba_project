# Plan de Cierre y Checklist de Entrega — REBA (ARC115)

- **Fecha límite de entrega:** Lunes 16 de noviembre, 6:00 p.m.
- **Horario de trabajo:** se inicia el **lunes ~06:00** (al llegar a la oficina, avisar para arrancar) y TODO debe estar listo antes de las **13:00**. Salida de casa 04:30–05:00 a.m., llegada ~06:00. Colchón 13:00–18:00 solo para imprevistos.
- **Domingo 15:** no requiere acción — `REBA_outdoor_v5` sigue entrenando solo. Para el lunes ~06:00 estará ~época 40+ (o terminado / early-stop ~ép 35–50 según `docs/plan_post_v5.md`).
- **Estado base:** `v5` sigue mejorando — mejor checkpoint **época 26** (`mAP50-95: 0.842`, `mAP50: 0.986`, `F1: 0.982` en val sintético); al 15-nov ~15:45 va en la época 30/50 (~54-70 min/época).
- **Estrategia:** al llegar, check-in del entorno → corte de `v5` → exportación ONNX → evaluación `real_water` + umbral → restart + `/upload` → E2E hardware → docs + commit, todo antes de las 13:00. `REBA_outdoor_v6` (GPU nube + `REBA_field`) queda como **Fase 2 / Trabajo Futuro**.

## Cronograma — Lunes 16

| Hora | Hito |
|---|---|
| 06:00 – 06:20 | Check-in: estado del training, health del server, túnel, git |
| 06:20 – 07:00 | Fase 1: corte de `v5` + exportación ONNX |
| 07:00 – 09:00 | Fase 2: evaluación `real_water` + umbral + restart + `/upload` |
| 09:00 – 10:15 | Fase 3: prueba E2E física presencial en la oficina (ESP32-CAM + bomba) |
| 10:15 – 12:30 | Fase 4: documentación + commit final |
| **13:00** | **LISTO** — colchón hasta las 18:00 |

> **Rama NO-GO:** si la Tarea 3 concluye que `v5` **no empata al baseline** en `real_water`, restaurar el baseline: en la Tarea 2 ya se archivaron el `.onnx` y `.pt` de `v2` — cópialos de vuelta desde `models/archive/` hacia `models/best.onnx` y `models/best.pt`, y NO actualices `INFERENCE_CONFIDENCE` (el server sigue corriendo el baseline en memoria, no hace falta reiniciar). Luego registrar la brecha en `PROGRESO.md` y `reports/experimentos.md`, y saltar directo a las Tareas 6–7 (la entrega documenta el modelo anterior + roadmap `v6` como contribución principal).

---

## Fase 1 — Lunes 06:20–07:00: Check-in, corte de `v5` y exportación ONNX

### - [ ] Tarea 1: Verificar estado del entrenamiento y detenerlo de forma segura
```text
Actúa como ingeniero de ML en el repo REBA. El entrenamiento `REBA_outdoor_v5` corrió toda la noche; primero verifica el estado general del entorno:
0. Check-in: ejecuta `ai_pipeline/train_monitor.py --run REBA_outdoor_v5 --once` con `.venv-reba\Scripts\python`, revisa `git status` rápido y confirma que el server sigue vivo (`GET http://localhost:5000/api/health`). Si el proceso de entrenamiento murió en la noche (PC suspendida/apagada), relanza primero con `retomar.bat` y espera a que complete la época en curso antes de cortar.
1. Analiza `runs/detect/REBA_outdoor_v5/results.csv` y el progreso de la época en curso. Para el lunes ~06:00 el run debería estar ~época 40+ o ya terminado (early-stop ~ép 35 o época 50).
2. Si el monitor reporta entrenamiento terminado/early-stop, no hay nada que detener — pasa directo al paso 3. Si sigue corriendo: si faltan menos de 15 minutos para que cierre la época actual, indícamelo para esperar; de lo contrario ejecuta `detener_entrenamiento.bat` (respalda `best.pt`/`last.pt` como `.bak` y mata ambos procesos python de `train_outdoor.py`, launcher + hijo).
3. Confirma que `runs/detect/REBA_outdoor_v5/weights/best.pt` existe, no está corrupto y reporta en qué época quedó el mejor checkpoint y sus métricas.
```

### - [ ] Tarea 2: Archivar modelo anterior (`v2`) y exportar `v5` a `models/best.onnx`
```text
Como el entrenamiento `REBA_outdoor_v5` se detuvo antes de completar las 50 épocas (o terminó por early-stop sin el flujo de exportación automático), realiza manualmente el flujo de exportación que hace `ai_pipeline/train_outdoor.py` al finalizar (ver líneas ~218-229: archiva con timestamp en models/archive/, exporta con imgsz=640, opset=12, simplify=True):
1. Archiva el `models/best.onnx` y `models/best.pt` actuales dentro de `models/archive/` con marca de tiempo para conservar el baseline `v2`.
2. Copia `runs/detect/REBA_outdoor_v5/weights/best.pt` a `models/best.pt` — CRÍTICO: `evaluate_outdoor.py` (`--new`) y `tune_confidence.py` (`--model`) usan `models/best.pt` por defecto; sin este paso ambas evaluaciones correrían sobre el modelo viejo `v2`.
3. Exporta ese `.pt` de v5 a formato ONNX usando Ultralytics con `imgsz=640`, `opset=12` y `simplify=True`; copia el `.onnx` generado a `models/best.onnx` y verifica con `onnxruntime` que carga correctamente en CPU y acepta tensores de entrada `(1, 3, 640, 640)` con 3 clases (`plastico`, `unicel`, `metal`). No modifiques `server/inference.py`.
```

---

## Fase 2 — Lunes 07:00–09:00: Evaluación en agua real y calibración

### - [ ] Tarea 3: Evaluar `v5` contra `real_water` y calcular el umbral óptimo de confianza
```text
Evalúa el nuevo modelo `REBA_outdoor_v5` sobre el dataset de validación en agua real (FloatWaste inédito) sin reiniciar todavía el servidor:
1. Ejecuta `ai_pipeline/evaluate_outdoor.py` para actualizar `outdoor_training_report.md` y extrae la comparación entre `v5` y los pesos archivados en `models/archive/` sobre el split `real_water`.
2. Ejecuta `ai_pipeline/tune_confidence.py --data datasets/REBA_real_val/data.yaml --split val --confs 0.15 0.20 0.25 0.30 0.35 0.45 0.55`.
3. Preséntame una tabla comparativa con `mAP50`, `mAP50-95`, `Precision`, `Recall`, `F1` y latencia en CPU, indicando explícitamente el veredicto go/no-go según los criterios de `docs/plan_post_v5.md` (sección 2) y cuál es el valor exacto recomendado para `INFERENCE_CONFIDENCE`.
```

### - [ ] Tarea 4: Aplicar nuevo umbral en `.env`, reiniciar servidor y validar `/upload`
```text
Actualiza la configuración de producción de REBA con los resultados calibrados de `v5`:
1. Actualiza `INFERENCE_CONFIDENCE` en `.env` con el umbral óptimo obtenido en `REBA_real_val` (manteniendo `MODEL_PATH=models/best.onnx` y `CLASS_NAMES=plastico,unicel,metal`). No subas `.env` a git.
2. Reinicia el proceso de Uvicorn (`server.main:app --host 0.0.0.0 --port 5000`) para que cargue el nuevo `models/best.onnx`. Hay dos procesos python de uvicorn (launcher + hijo): reinicia ambos o mata el launcher y relanza con `campo.bat`.
3. Verifica `GET http://localhost:5000/api/health` confirmando `backend: onnx` y `ok: true`.
4. Ejecuta `ai_pipeline/eval_upload_path.py --dataset datasets/REBA_real_val --split val` para validar el pipeline real de producción (decodificación JPEG + ONNX + NMS + umbral `.env`) y confirma que no haya degradación respecto a la evaluación offline.
```

---

## Fase 3 — Lunes 09:00–10:15 EN LA OFICINA: Pruebas E2E con hardware (presencial)

> Todo el hardware (ESP32-CAM, HC-SR04, bomba 12 V) está en la oficina junto al usuario — la prueba es presencial, sin acceso remoto. El ESP32 usa WiFiManager: si la WiFi guardada no coincide con la red de la oficina, tras ~10 min reabre el portal de provisión (reconfigurar con el celular). Además descarga `server_url` y config del server cada 60 s vía `device-config`, así que el server solo necesita ser alcanzable desde el LAN local (o un hotspot compartido si el ESP32 queda fuera de ella — el túnel zrok es solo HTTPS y no lo usa el firmware).

### - [ ] Tarea 5: Verificar conectividad ESP32-CAM, ciclo de bomba NC y túnel
```text
Audita el entorno físico de REBA para la prueba E2E con la ESP32-CAM, el sensor ultrasónico HC-SR04 y la bomba de 12 V, todos en la oficina:
0. Conectividad de red: enciende el ESP32 y verifica que se asocia a la WiFi de la oficina. Si levanta el portal WiFiManager (AP propio), reconfigúralo desde el celular con la red local y la `server_url` correcta (IP local del server o URL del túnel). Confirma que el ESP32 empieza a reportar (`/api/captures/latest`, `/api/pump/events`, `/api/stats` muestran actividad reciente).
1. Consulta `GET /api/config`, `GET /api/pump-status` y `GET /api/admin/device-config` para verificar que los parámetros del servidor (`dist_min_cm=0`, `dist_max_cm=23`, `pump_photos=3`, `pump_seconds=10`) estén alineados con los que el ESP32 descarga del device-config y con el firmware `esp32_firmware/fotos_nc/fotos_nc.ino`.
2. Verifica que el ciclo de bomba NC responde al rango de distancia configurado (acercar/alejar un objeto frente al HC-SR04) y que las capturas llegan a `/upload` con detecciones del nuevo `models/best.onnx`.
3. Ejecuta la prueba física con muestras de plástico, unicel y metal mientras monitoreas `/api/captures/latest`, `/api/pump/events` y `/api/stats` en tiempo real. Si `logs/tunnel.log` existe o hay un túnel zrok activo, extrae la URL pública vigente para acceso del celular al dashboard. No modifiques el firmware `.ino`.
4. Al terminar, captura el estado final (`/api/stats`, últimos `/api/captures/latest`, `/api/pump/events`) y pégalo en `PROGRESO.md` como evidencia E2E.
```

---

## Fase 4 — Lunes 10:15–12:30: Documentación final y cierre Git — **deadline 12:45**

### - [ ] Tarea 6: Actualizar `README.md`, `PROGRESO.md` y `reports/experimentos.md` con métricas `v5` y roadmap `v6`
```text
Actualiza la documentación del proyecto REBA para reflejar el estado final de entrega con `REBA_outdoor_v5`:
1. En `README.md`, actualiza la sección "3. Modelo de visión" (que aún menciona "outdoor v2") para documentar `REBA_outdoor_v5`, incluyendo sus métricas finales en validación sintética y en `real_water` (`REBA_real_val`), así como el umbral `INFERENCE_CONFIDENCE` calibrado en `.env`.
2. Actualiza `PROGRESO.md` y `reports/experimentos.md` registrando el veredicto go/no-go de `v5` (según `docs/plan_post_v5.md`), la brecha de dominio observada entre imágenes sintéticas y agua real, y posicionando `REBA_outdoor_v6` (`docs/plan_nube_colab_kaggle.md` + recolección de `REBA_field` vía Exportar YOLO en GPU) como Fase 2 / Trabajo Futuro. Actualiza `docs/manual_alcances.md` solo si algo del alcance cambió.
3. Si la prueba E2E física quedó parcial o no se ejecutó, documéntalo honestamente como "verificado parcialmente" con la evidencia recolectada.
4. Mantén el tono técnico, conciso y consistente con el estilo existente del repositorio.
```

### - [ ] Tarea 7: Auditoría de higiene del repositorio y commit final de entrega
```text
Realiza una revisión final de git antes de la entrega académica de REBA:
1. Ejecuta `git status` y `git diff --stat` para revisar todos los archivos modificados y nuevos.
2. Verifica que `.gitignore` esté bloqueando correctamente los artefactos de runtime: `.env`, `data/` (incluye `REBA.db`), `logs/`, `runs/` (cubre los `*.bak` de checkpoints), `models/**/*.onnx` y `models/**/*.pt`, `datasets/*`. Ojo: no existe un patrón `*.bak` global — los `.bak` quedan cubiertos por `runs/`; `logs/` sí está ignorado, verifícalo explícitamente.
3. Sobre `models/best.onnx`: está cubierto por `models/**/*.onnx` en `.gitignore`. Para una entrega autocontenida se recomienda incluir el artefacto final con `git add -f models/best.onnx` (~12 MB, dentro del límite de GitHub); si se decide NO versionarlo (convención del repo: modelos pesados locales), documenta en `README.md` el comando exacto para exportarlo. Haz staging de todo lo demás (código, docs, scripts, `checklist_de_entrega_y_prompts_swe_2_fast.md`).
4. Genera un commit limpio y descriptivo de cierre para `REBA_outdoor_v5`. Muéstrame el resumen final del commit y el árbol limpio (`git status` vacío salvo ignorados).
```

---

## Matriz de riesgos y mitigaciones

Leyenda: **P** = probabilidad (A alta / M media / B baja) · **I** = impacto en la entrega (A alto / M medio / B bajo). Las mitigaciones referencian la tarea donde se cubren.

### A. Entorno y procesos

| # | Riesgo | P | I | Mitigación |
|---|---|---|---|---|
| A1 | El entrenamiento muere en la noche (suspensión, apagón, crash, Windows Update) | M | A | `retomar.bat` reanuda desde la última época completa (Tarea 1 paso 0). Verificar proceso vivo en el check-in de las 06:00. En laptop: no suspender con tapa cerrada durante el trayecto. |
| A2 | `best.pt`/`last.pt` corruptos por kill a mitad de escritura | B | A | `detener_entrenamiento.bat` respalda `.bak` antes de matar; verificar que `best.pt` carga en `torch.load` antes de exportar (Tarea 1 paso 3); fallback a `.bak` o a `best_*` archivado. |
| A3 | Quedan procesos python huérfanos (train, uvicorn, monitor) | M | M | El `.bat` mata launcher+hijo de train; para uvicorn matar todos los PID y verificar `netstat -ano \| findstr :5000` libre antes de relanzar con `campo.bat` (Tarea 4). |
| A4 | El server no levanta tras el restart (ONNX incompatible, dependencia faltante, puerto ocupado) | M | A | `GET /api/health` lo detecta de inmediato (Tarea 4 paso 3). Rollback: restaurar `best_*.onnx`/`best_*.pt` archivados en Tarea 2 y volver a `INFERENCE_CONFIDENCE` anterior. |
| A5 | Exportación ONNX falla (`simplify`, opset, onnxslim faltante) | B | M | Reintentar sin `simplify=True`; si persiste, exportar con opset 11/13 y verificar que `onnxruntime` del server lo carga (Tarea 2 paso 3). |
| A6 | Disco lleno al exportar/archivar (runs + archive crecen ~decenas de MB) | B | M | `Get-PSDrive C` / `dir` rápido en el check-in; los `.pt` ~6 MB y `.onnx` ~12 MB, hay margen típico suficiente. |
| A7 | Evaluaciones lentas por CPU ocupada si se corren en paralelo al training | M | M | Regla: matar el training ANTES de lanzar cualquier evaluación y correr todo secuencial (Tareas 1→2→3). |

### B. Modelo, evaluación y despliegue

| # | Riesgo | P | I | Mitigación |
|---|---|---|---|---|
| B1 | **`evaluate_outdoor.py` y `tune_confidence.py` evalúan el modelo VIEJO** porque usan `models/best.pt` por defecto | A (si se omite el paso) | A | Corregido en Tarea 2 paso 2: copiar `runs/.../best.pt` → `models/best.pt` es obligatorio antes de evaluar. Verificar con `ls -la models/best.pt` (mtime reciente). |
| B2 | NO-GO con `models/best.onnx`/`best.pt` ya sobrescritos por v5 | M | A | Rama NO-GO restauración explícita: copiar de vuelta los `best_*` archivados en Tarea 2 (son v2). El server sigue con v2 en memoria, no requiere restart. |
| B3 | Umbral óptimo cae en el borde del sweep (0.15 o 0.55) | M | M | Extender el sweep (`--confs 0.05 0.10` o `0.65 0.70`) antes de fijar `INFERENCE_CONFIDENCE`; un óptimo en el borde indica rango insuficiente. |
| B4 | `v5` empata en agregado pero colapsa una clase (ej. `metal`) en `real_water` | M | M | Revisar métricas por clase en el reporte; el criterio go/no-go es agregado + no empeorar baseline, pero documentar la clase débil en `PROGRESO.md` honestamente. |
| B5 | `evaluate_outdoor.py` tarda >45 min (multi-dataset en CPU) | M | M | Si se alarga, priorizar `tune_confidence.py` + `eval_upload_path.py` (son lo que bloquea el despliegue) y dejar el reporte parcial (contingencia en Tarea 3). |
| B6 | `eval_upload_path.py` corre contra el server con el modelo VIEJO | M | A | Orden ya correcto: va después del restart en Tarea 4 paso 4 (pega a `localhost:5000` en vivo). Nunca correrlo antes del restart. |
| B7 | Confundir época de corte con época del mejor checkpoint | B | B | `best.pt` guarda la mejor época de validación (no la última); reportar en docs la época real leída de `results.csv`/monitor, no la del corte. |
| B8 | `INFERENCE_CONFIDENCE` mal escrito en `.env` (comillas, espacios, formato) | B | M | Editar solo esa línea `KEY=valor`; verificar el valor cargado vía `GET /api/config` o logs del server tras el restart. Anotar el valor anterior (~0.35) antes de editar — NO crear `.env.bak` porque `.env.bak` NO está cubierto por `.gitignore` y podría commitearse. |

### C. Hardware y E2E (oficina, lunes ~09:00)

| # | Riesgo | P | I | Mitigación |
|---|---|---|---|---|
| C1 | El ESP32 no toma la WiFi de la oficina (credenciales distintas, red enterprise/captive portal) | M | A | Portal WiFiManager + celular (Tarea 5 paso 0). Fallback: hotspot del celular uniendo ESP32 y laptop a la misma red (el túnel zrok es solo HTTPS — no sirve para el ESP32). |
| C2 | Firewall de Windows bloquea `:5000` entrante desde el ESP32 (perfil público de red de oficina) | M | A | Detectarlo en Tarea 5 paso 0/2: si el ESP32 no alcanza `/api/health`, unir la PC al hotspot del celular junto al ESP32 (LAN compartida, sin abrir puertos) o regla de firewall entrante (requiere admin). El túnel zrok no aplica al ESP32. |
| C3 | `server_url` del ESP32 desactualizado (IP del laptop cambió por DHCP) | M | M | El ESP32 descarga config cada 60 s: actualizar `server_url` vía `GET/POST /api/admin/device-config` a la IP actual y verificar que `/api/captures/latest` muestre actividad. |
| C4 | Bomba no dispara o dispara incorrectamente (NC vs NO, umbrales de distancia, relé) | M | M | Verificar `BOMBA_EN_NC` del firmware vs config del server (`dist_min_cm=0`, `dist_max_cm=23`, `pump_photos=3`, `pump_seconds=10`); prueba con objeto a distintas distancias; confirmar clic del relé y alimentación 12 V adecuada. |
| C5 | ESP32 en boot loop por brownout (cámara + WiFi + flash sobre fuente débil) | B | M | Fuente 5 V/2 A decente; si las capturas cesan abruptamente, power cycle y revisar que vuelva a reportar. |
| C6 | No hay muestras físicas de plástico/unicel/metal disponibles | M | B | Proxies honestos: botella PET (plástico), foam/unicel, cubierto metálico. Documentar la sustitución en `PROGRESO.md`. |
| C7 | La prueba física se alarga más que la ventana 09:00–10:15 | B | M | Cada ciclo = `pump_photos=3` × `pump_seconds=10` + intervalos; si se atrasa, recortar a 1 muestra por material y documentar cobertura parcial. |
| C8 | Capturas llegan pero sin detecciones (confianzas bajas en campo) | M | B | Es evidencia de la brecha de dominio ya documentada — NO "arreglar" el umbral sobre la marcha (ya calibrado en `real_water`); registrarlo en `PROGRESO.md`. |

### D. Git y entrega académica

| # | Riesgo | P | I | Mitigación |
|---|---|---|---|---|
| D1 | `models/best.onnx` queda fuera del commit por `.gitignore` (`models/**/*.onnx`) | A | M | Decisión explícita en Tarea 7 paso 3: `git add -f models/best.onnx` (~12 MB, entrega autocontenida) o documentar el comando de exportación en README. |
| D2 | Commit arrastra secretos o archivos pesados por descuido | B | A | `.gitignore` ya cubre `.env`, `data/`, `logs/`, `runs/`, `datasets/*`, `models/**`; revisar `git status` + `git diff --cached --stat` antes de commitear; jamás `git add -f` indiscriminado; ningún archivo >50 MB (límite GitHub). |
| D3 | Identidad git no configurada → commit falla | B | M | `git -c user.name="..." -c user.email="..." commit` one-off, o consultar al usuario qué identidad usar. |
| D4 | Docs quedan con métricas viejas o de la época 20 del encabezado | M | M | Copiar números REALES de `results.csv` / `outdoor_training_report.md` / salida de la Tarea 3; grep final por `v2`/`outdoor v2` residual en `README.md` y `docs/`. |
| D5 | La entrega exige push a un remoto (GitHub/Classroom) que no está configurado | M | M | Verificar `git remote -v`; hacer push SOLO si la entrega lo requiere — confirmar con el usuario el mecanismo de entrega antes de las 12:45. |
| D6 | El checklist queda desactualizado respecto a lo ejecutado (p.ej. v5 terminó solo) | M | B | La Tarea 1 contempla ambos caminos; registrar en `PROGRESO.md` qué camino real se tomó. |

### E. Tiempo y ejecución del plan

| # | Riesgo | P | I | Mitigación |
|---|---|---|---|---|
| E1 | Check-in de las 06:00 se retrasa (trayecto, conexión) | M | M | El deadline real es 13:00, no las 06:00; comprimir bloques: si se arranca 07:00, solapar la espera de evaluaciones con el borrador de README/PROGRESO (Tarea 6). |
| E2 | Las evaluaciones CPU (Tarea 3) exceden su ventana de ~2 h | M | A | Semáforo: si `evaluate_outdoor` pasa de ~45 min, abortar y quedarse con `tune_confidence` + `eval_upload_path`; el umbral y el path de producción son lo bloqueante. |
| E3 | La laptop se suspende o apaga en la oficina a media evaluación | B | M | Mantener conectada a corriente; los scripts no tienen checkpoint — reejecutar el que se interrumpa (son idempotentes). |
| E4 | Usuario se distrae o la prueba física no se completa | M | B | E2E documentado como "parcial" con la evidencia recolectada; NO bloquea el commit (Tarea 6 paso 3). |
| E5 | Se pasa de las 12:45 con trabajo pendiente | M | A | Regla de congelamiento: commit con lo que haya, docs honestas sobre lo pendiente, colchón 13:00–18:00 solo para arreglos menores. |
| E6 | Otra sesión/proceso edita los mismos archivos en paralelo | B | M | Una sola sesión de agente activa sobre el repo; antes del commit, `git diff` final para confirmar que nada externo tocó archivos. |

### F. Domingo por la noche (prevención)

| # | Riesgo | P | I | Mitigación |
|---|---|---|---|---|
| F1 | La PC se suspende/apaga durante la noche de entrenamiento | M | A | Antes de dormir: `powercfg /change standby-timeout-ac 0` (o verificar plan de energía), laptop conectada a corriente; si aun así muere, `retomar.bat` el lunes (Tarea 1 paso 0). |
| F2 | El entrenamiento llega a época 50 o early-stop y auto-exporta | M | B | Es el camino feliz: `train_outdoor.py` ya archiva y exporta solo; la Tarea 2 se reduce a verificar timestamps y que `models/best.onnx` sea de v5. |
