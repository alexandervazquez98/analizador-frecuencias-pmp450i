"""
tests/conftest.py — Fixtures compartidos para todos los tests.

Maneja la configuración del entorno de pruebas y mocks necesarios
para que los módulos de la app se importen correctamente en entorno local.
"""

import sys
import os
import pytest

# Agregar el directorio raíz al path para imports relativos
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
