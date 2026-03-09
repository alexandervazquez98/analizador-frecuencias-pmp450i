"""
Módulo de inicialización de la aplicación
"""

from flask import Flask
from flask_cors import CORS
import os
import logging
from logging.handlers import RotatingFileHandler


def configure_logging():
    """
    Configura el logging centralizado de la aplicación.

    - Logs técnicos van a logs/app.log (RotatingFileHandler) y a consola.
    - audit_logs.jsonl se mantiene completamente separado (gestionado por AuditManager).

    Esta función debe llamarse UNA sola vez al inicio de la aplicación,
    reemplazando cualquier logging.basicConfig() disperso en módulos individuales.
    """
    log_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"
    )
    os.makedirs(log_dir, exist_ok=True)

    log_file = os.path.join(log_dir, "app.log")
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    formatter = logging.Formatter(log_format)

    # Handler: archivo rotativo (5 MB max, 3 backups)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    # Handler: consola (mantiene comportamiento original)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # Configurar root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Limpiar handlers previos para evitar duplicados si se llama más de una vez
    root_logger.handlers.clear()

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logging.getLogger(__name__).info(
        f"Logging centralizado configurado: archivo={log_file}, consola=True"
    )


def create_app():
    """Factory para crear la aplicación Flask"""
    # Configurar logging centralizado ANTES de crear la app
    configure_logging()

    app = Flask(__name__, template_folder="templates", static_folder="../static")

    # Configuración
    app.config["SECRET_KEY"] = os.environ.get(
        "SECRET_KEY", "dev-secret-key-change-in-production"
    )
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB max

    # Habilitar CORS
    CORS(app)

    return app
