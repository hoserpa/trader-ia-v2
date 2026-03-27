"""Inferencia del modelo LightGBM entrenado."""
import os
import json
import warnings
import joblib
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np
from loguru import logger
from config import config

warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")


class ModelPredictor:
    """Carga el modelo LightGBM y realiza inferencia de señales de trading."""

    SIGNAL_MAP = {0: "SELL", 1: "HOLD", 2: "BUY"}

    def __init__(self):
        self.model = None
        self.scaler = None
        self.metadata = {}
        self.feature_names = []
        self._load()

    def _load(self) -> None:
        if not os.path.exists(config.model.model_path):
            logger.warning(f"Modelo no encontrado en {config.model.model_path}. El bot funcionará en modo espera.")
            return
        try:
            self.model = joblib.load(config.model.model_path)
            self.scaler = joblib.load(config.model.scaler_path)

            metadata_path = config.model.model_path.replace("trained_model.pkl", "model_metadata.json")
            if os.path.exists(metadata_path):
                with open(metadata_path) as f:
                    self.metadata = json.load(f)
                self.feature_names = self.metadata.get("feature_cols", self.metadata.get("feature_names", []))

            logger.info(f"Modelo cargado. Entrenado: {self.metadata.get('trained_at', 'desconocido')}")
            logger.info(f"Métricas validación: {self.metadata.get('validation_metrics', {})}")
            logger.info(f"Feature names: {self.feature_names[:5]}...")
        except Exception as e:
            logger.error(f"Error cargando modelo: {e}")
            self.model = None
            self.scaler = None

    def is_model_loaded(self) -> bool:
        loaded = self.model is not None and self.scaler is not None
        if not loaded:
            logger.warning(f"is_model_loaded(): False - model={self.model is not None}, scaler={self.scaler is not None}")
        return loaded

    def predict(self, features: pd.Series) -> Optional[dict]:
        """Realiza predicción para el vector de features dado.
        Retorna dict con signal, confidence y probabilidades, o None si el modelo no está cargado.
        """
        if not self.is_model_loaded():
            return None

        try:
            if self.feature_names:
                feature_vector = []
                for name in self.feature_names:
                    feature_vector.append(features.get(name, 0.0))
                X = np.array([feature_vector])
            else:
                X = features.values.reshape(1, -1)

            X_scaled = self.scaler.transform(X)

            if hasattr(self.model, 'predict_proba'):
                probs = self.model.predict_proba(X_scaled)[0]
                class_order = self.model.classes_
                prob_dict = {self.SIGNAL_MAP[int(c)]: float(p) for c, p in zip(class_order, probs)}
                best_class = int(class_order[np.argmax(probs)])
                confidence = float(np.max(probs))
            else:
                predictions = self.model.predict(X_scaled)
                logger.debug(f"Predictions type: {type(predictions)}, shape: {predictions.shape}, value: {predictions}")
                pred_array = predictions[0] if predictions.ndim > 1 else predictions
                best_class = int(np.argmax(pred_array))
                confidence = 0.8
                prob_dict = {self.SIGNAL_MAP[best_class]: confidence}
                for other_class in [0, 1, 2]:
                    if other_class != best_class:
                        prob_dict[self.SIGNAL_MAP[other_class]] = (1 - confidence) / 2
            
            signal = self.SIGNAL_MAP[best_class]
            
            logger.debug(f"Raw predictions: best_class={best_class}, confidence={confidence}")

            if signal == "BUY" and confidence < config.risk.buy_threshold:
                signal = "HOLD"
            elif signal == "SELL" and confidence < config.risk.sell_threshold:
                signal = "HOLD"

            return {
                "signal": signal,
                "confidence": confidence,
                "probabilities": prob_dict,
                "timestamp": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.error(f"Error en predicción: {e}")
            return None

    def reload_if_updated(self) -> None:
        """Recarga el modelo si el archivo ha sido actualizado."""
        if not os.path.exists(config.model.model_path):
            logger.warning("reload_if_updated: modelo no encontrado")
            return
        mtime = os.path.getmtime(config.model.model_path)
        if mtime > self.metadata.get("_file_mtime", 0):
            logger.info("Modelo actualizado detectado, recargando...")
            self._load()
            if self.is_model_loaded():
                self.metadata["_file_mtime"] = mtime
                logger.info("Modelo recargado correctamente")
            else:
                logger.error("Error al recargar el modelo")

    def get_model_metadata(self) -> dict:
        return {
            "loaded": self.is_model_loaded(),
            "trained_at": self.metadata.get("trained_at"),
            "validation_metrics": self.metadata.get("validation_metrics", {}),
            "feature_count": len(self.feature_names),
            "model_path": config.model.model_path,
        }
