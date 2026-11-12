#!/usr/bin/env python3
"""
REBA — Definición compartida de las transformaciones "outdoor" (albumentations).

Simula las condiciones reales a la intemperie que el dataset sintético no cubre:

- Iluminación severa  → RandomBrightnessContrast, RandomGamma, RandomShadow,
                        RandomSunFlare
- Clima / ambiente    → RandomFog, RandomRain, Defocus, MotionBlur
                        (cámara en movimiento / viento sobre el agua)
- Desgaste por el sol → HueSaturationValue, ToGray
- Sensor ESP32-CAM    → GaussNoise, ImageCompression (JPEG agresivo)
- Oclusión parcial    → CoarseDropout (basura semienterrada, tapada por
                        hojas o tierra)  +  erasing/cutmix de Ultralytics

Todas las transformaciones son de nivel píxel (no espaciales): las cajas YOLO
no se modifican, así que el pipeline es seguro para detección.

`outdoor_train_transforms()` devuelve la lista que se pasa a
`model.train(augmentations=...)` en train_outdoor.py — Ultralytics la aplica
en cada época a través de su integración Albumentations.

`outdoor_eval_transforms()` devuelve una versión más severa, con efectos casi
forzados, para construir el conjunto de validación difícil
(build_outdoor_eval.py → datasets/REBA_outdoor_eval).

Nota sobre colores: Ultralytics entrega imágenes BGR a Albumentations, así que
`fill` de CoarseDropout va en orden BGR — (35, 65, 95) es un marrón barro/hoja.
"""

from __future__ import annotations

from typing import List

import albumentations as A

# Tono "tierra/hojas" en BGR para simular oclusión por material natural.
OCCLUSION_FILL_BGR = (35, 65, 95)


def outdoor_train_transforms() -> List[A.BasicTransform]:
    """Pipeline de aumento para entrenamiento (aleatorio por época)."""
    return [
        # --- Iluminación severa ---
        A.RandomBrightnessContrast(
            brightness_limit=(-0.45, 0.45),
            contrast_limit=(-0.45, 0.45),
            p=0.65,
        ),
        A.RandomGamma(gamma_limit=(60, 140), p=0.35),
        A.RandomShadow(
            shadow_roi=(0, 0, 1, 1),
            num_shadows_limit=(1, 3),
            shadow_intensity_range=(0.25, 0.75),
            p=0.45,
        ),
        A.RandomSunFlare(
            flare_roi=(0, 0, 1, 0.6),
            src_radius=250,
            num_flare_circles_range=(4, 8),
            p=0.12,
        ),
        # --- Clima / ambiente / cámara en movimiento ---
        A.OneOf(
            [
                A.RandomFog(fog_coef_range=(0.15, 0.45), alpha_coef=0.10, p=1.0),
                A.RandomRain(
                    slant_range=(-15, 15),
                    drop_width=1,
                    blur_value=3,
                    brightness_coefficient=0.8,
                    p=1.0,
                ),
                A.Defocus(radius=(2, 5), p=1.0),
            ],
            p=0.30,
        ),
        A.MotionBlur(blur_limit=(3, 11), p=0.30),
        # --- Color / basura desgastada por el sol ---
        A.HueSaturationValue(
            hue_shift_limit=(-25, 25),
            sat_shift_limit=(-45, 45),
            val_shift_limit=(-35, 35),
            p=0.55,
        ),
        A.ToGray(p=0.03),
        # --- Ruido y compresión del sensor (ESP32-CAM JPEG) ---
        A.GaussNoise(std_range=(0.03, 0.10), p=0.35),
        A.ImageCompression(
            compression_type="jpeg", quality_range=(35, 90), p=0.40
        ),
        # --- Oclusión parcial (hojas, tierra, basura semienterrada) ---
        A.CoarseDropout(
            num_holes_range=(1, 4),
            hole_height_range=(0.05, 0.18),
            hole_width_range=(0.05, 0.18),
            fill=OCCLUSION_FILL_BGR,
            p=0.45,
        ),
    ]


def outdoor_eval_transforms() -> List[A.BasicTransform]:
    """Pipeline severo y casi forzado para el conjunto de evaluación difícil."""
    return [
        A.RandomBrightnessContrast(
            brightness_limit=(-0.35, 0.35),
            contrast_limit=(-0.35, 0.35),
            p=1.0,
        ),
        A.RandomGamma(gamma_limit=(70, 140), p=0.8),
        A.RandomShadow(
            shadow_roi=(0, 0, 1, 1),
            num_shadows_limit=(1, 2),
            shadow_intensity_range=(0.3, 0.7),
            p=0.65,
        ),
        A.OneOf(
            [
                A.RandomFog(fog_coef_range=(0.2, 0.4), alpha_coef=0.10, p=1.0),
                A.RandomRain(rain_type="default", blur_value=4, p=1.0),
                A.MotionBlur(blur_limit=(5, 11), p=1.0),
            ],
            p=0.55,
        ),
        A.HueSaturationValue(
            hue_shift_limit=(-25, 25),
            sat_shift_limit=(-45, 45),
            val_shift_limit=(-35, 35),
            p=0.9,
        ),
        A.GaussNoise(std_range=(0.04, 0.10), p=0.65),
        A.ImageCompression(
            compression_type="jpeg", quality_range=(35, 70), p=1.0
        ),
        A.CoarseDropout(
            num_holes_range=(1, 4),
            hole_height_range=(0.06, 0.18),
            hole_width_range=(0.06, 0.18),
            fill=OCCLUSION_FILL_BGR,
            p=0.7,
        ),
    ]
