from flask import Flask, request, jsonify, send_from_directory
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import SOLAR_PV_KW, INVERTER_KW, BATTERY_KWH
from web.simulate_logic import simulate_sigen_decision

app = Flask(__name__, static_folder='static')

@app.route('/config', methods=['GET'])
def get_config():
    # Return system specs as config
    return jsonify({
        'inverter_kw': INVERTER_KW,
        'battery_kwh': BATTERY_KWH,
        'solar_pv_kw': SOLAR_PV_KW,
    })

@app.route('/simulate', methods=['POST'])
def simulate():
    data = request.json
    def safe_float(val, default):
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
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/<path:path>', methods=['GET'])
def serve_static(path):
    return send_from_directory(app.static_folder, path)

if __name__ == '__main__':
    app.run(debug=True)
