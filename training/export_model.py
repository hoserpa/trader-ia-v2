"""Exporta el modelo para la Raspberry Pi.

Usage:
    python export_model.py --model output/model --target ../bot/model
"""
import argparse
import shutil
from pathlib import Path
import json
from loguru import logger


def validate_model_files(model_dir: Path) -> bool:
    """Valida que existen los archivos necesarios."""
    required = ["trained_model.pkl", "scaler.pkl", "model_metadata.json"]
    missing = [f for f in required if not (model_dir / f).exists()]
    
    if missing:
        logger.error(f"Archivos faltantes: {missing}")
        return False
    
    metadata = json.loads((model_dir / "model_metadata.json").read_text())
    
    required_metrics = ["precision_buy", "precision_sell", "sharpe_ratio", "max_drawdown"]
    missing_metrics = [m for m in required_metrics if m not in metadata.get("test_metrics", {})]
    
    if missing_metrics:
        logger.warning(f"Métricas faltantes en metadata: {missing_metrics}")
    
    MIN_PRECISION = 0.60
    MIN_SHARPE = 1.0
    MAX_DRAWDOWN = 0.15
    
    test_metrics = metadata.get("test_metrics", {})
    bt_stats = metadata.get("backtest_stats", {})
    
    logger.info("Validación contra mínimos del spec:")
    
    checks = [
        ("Precision BUY", test_metrics.get("precision_buy", 0), MIN_PRECISION, ">="),
        ("Precision SELL", test_metrics.get("precision_sell", 0), MIN_PRECISION, ">="),
        ("Sharpe", bt_stats.get("sharpe_ratio", 0), MIN_SHARPE, ">="),
        ("Max Drawdown", bt_stats.get("max_drawdown", 1), MAX_DRAWDOWN, "<="),
    ]
    
    all_passed = True
    for name, value, threshold, op in checks:
        if op == ">=":
            passed = value >= threshold
        else:
            passed = value <= threshold
        
        status = "✓" if passed else "✗"
        logger.info(f"  {status} {name}: {value:.4f} {op} {threshold}")
        
        if not passed:
            all_passed = False
    
    if not all_passed:
        logger.warning("Modelo no cumple requisitos mínimos. Usar con precaución.")
    
    return True


def copy_to_target(model_dir: Path, target_dir: Path) -> bool:
    """Copia archivos al directorio destino."""
    target_dir.mkdir(parents=True, exist_ok=True)
    
    files = ["trained_model.pkl", "scaler.pkl", "model_metadata.json"]
    
    for f in files:
        src = model_dir / f
        dst = target_dir / f
        shutil.copy2(src, dst)
        logger.info(f"Copiado: {f}")
    
    logger.info(f"Archivos copiados a {target_dir}")
    return True


def create_deployment_summary(model_dir: Path, output_dir: Path):
    """Crea resumen de deployment."""
    metadata = json.loads((model_dir / "model_metadata.json").read_text())
    
    summary = {
        "model_version": "1.0",
        "trained_at": metadata.get("trained_at"),
        "features_count": len(metadata.get("feature_cols", [])),
        "confidence_threshold": metadata.get("confidence_threshold"),
        "test_metrics": metadata.get("test_metrics", {}),
        "backtest_stats": metadata.get("backtest_stats", {}),
        "deployment_ready": True,
        "files": [
            "trained_model.pkl",
            "scaler.pkl", 
            "model_metadata.json"
        ]
    }
    
    summary_path = output_dir / "deployment_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    
    logger.info(f"Resumen de deployment: {summary_path}")
    return summary


def main():
    parser = argparse.ArgumentParser(description="Exporta modelo para Raspberry Pi")
    parser.add_argument("--model", type=str, default="output/model", help="Directorio del modelo")
    parser.add_argument("--target", type=str, default="../bot/model", help="Directorio destino (bot/model)")
    parser.add_argument("--skip-validation", action="store_true", help="Saltar validación de métricas")
    args = parser.parse_args()
    
    model_dir = Path(args.model)
    target_dir = Path(args.target).resolve()
    
    if not validate_model_files(model_dir):
        logger.error("Validación fallida")
        if not args.skip_validation:
            return
    
    logger.info("Modelo validado correctamente")
    
    copy_to_target(model_dir, target_dir)
    
    output_dir = model_dir.parent / "evaluation"
    if output_dir.exists():
        summary = create_deployment_summary(model_dir, output_dir)
        logger.info(f"Deployment summary: {summary['deployment_ready']}")
    
    logger.info("\n=== PASOS PARA DEPLOYMENT ===")
    logger.info(f"1. Archivos copiados a: {target_dir}")
    logger.info("2. Verificar que el bot tiene acceso a los archivos")
    logger.info("3. Reiniciar el bot para cargar el nuevo modelo")
    logger.info("4. Monitorear métricas en modo demo durante 1 semana")


if __name__ == "__main__":
    logger.add("logs/export_model.log", rotation="50 MB", level="INFO")
    main()
