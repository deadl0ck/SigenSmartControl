"""
app.py
------
Flask web server providing REST API and UI for Sigen inverter simulation and configuration.

Endpoints:
  GET  /config            - Returns system configuration (inverter, battery, solar specs)
  POST /simulate          - Accepts forecast and system parameters, returns mode decisions
  GET  /                  - Serves main UI (index.html)
  GET  /<path>            - Serves static assets (CSS, JavaScript, etc.)
"""

from flask import Flask, request, jsonify, send_from_directory
import sys
import os
from typing import Any
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config.settings import SOLAR_PV_KW, INVERTER_KW, BATTERY_KWH
from web.simulate_logic import simulate_sigen_decision

app = Flask(__name__, static_folder='static')

@app.route('/config', methods=['GET'])
def get_config():
    """Return system hardware configuration.
    
    Returns:
        JSON object with keys:
          - inverter_kw: Inverter capacity in kW
          - battery_kwh: Battery capacity in kWh
          - solar_pv_kw: Solar PV system capacity in kW
    """
    # Return system specs as config
    return jsonify({
        'inverter_kw': INVERTER_KW,
        'battery_kwh': BATTERY_KWH,
        'solar_pv_kw': SOLAR_PV_KW,
    })

@app.route('/simulate', methods=['POST'])
def simulate():
    """Simulate mode decisions for a given scenario.
    
    Request body (JSON):
      - inverter_kw: Override inverter capacity (optional)
      - battery_kwh: Override battery capacity (optional)
      - solar_pv_kw: Override solar capacity (optional)
      - soc: Battery state-of-charge 0-100 (optional, default 80)
      - forecast_morn: Morning forecast ('Green', 'Amber', 'Red')
      - forecast_aftn: Afternoon forecast
      - forecast_eve: Evening forecast
      
    Returns:
        JSON object with mode decisions for each period.
    """
    data = request.json
    def safe_float(val: Any, default: float) -> float:
        """Safely convert a value to float, with fallback default.
        
        Args:
            val: Value to convert (may be None, empty string, or numeric).
            default: Default value to return if conversion fails.
            
        Returns:
            Converted float value or the default.
        """
        try:
            if val is None or val == '':
                return float(default)
            return float(val)
        except Exception:
            return float(default)

    result = simulate_sigen_decision(
        inverter_kw=safe_float(data.get('inverter_kw'), INVERTER_KW),
        battery_kwh=safe_float(data.get('battery_kwh'), BATTERY_KWH),
        solar_pv_kw=safe_float(data.get('solar_pv_kw'), SOLAR_PV_KW),
        soc=safe_float(data.get('soc'), 80),
        forecast_morn=data.get('forecast_morn', 'Green'),
        forecast_aftn=data.get('forecast_aftn', 'Amber'),
        forecast_eve=data.get('forecast_eve', 'Red')
    )
    return jsonify(result)

@app.route('/', methods=['GET'])
def serve_index():
    """Serve the main UI page (index.html)."""
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/<path:path>', methods=['GET'])
def serve_static(path):
    """Serve static assets (CSS, JavaScript files, images, etc.).
    
    Args:
        path: File path relative to the static/ directory.
        
    Returns:
        The requested file, or 404 if not found.
    """
    return send_from_directory(app.static_folder, path)

if __name__ == '__main__':
    app.run(debug=True)
