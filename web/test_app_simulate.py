"""Unit tests for the web simulation API (web/app.py).

Tests REST endpoints for configuration and mode simulation.
"""

import os
import sys
import pytest
import json

# Ensure parent directory is in sys.path so we can import app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import app as flask_app

def post_simulate(client, payload):
    return client.post('/simulate', data=json.dumps(payload), content_type='application/json')

@pytest.fixture
def client():
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as client:
        yield client

def test_simulate_valid_soc(client):
    resp = post_simulate(client, {
        'inverter_kw': 5.5,
        'battery_kwh': 24,
        'solar_pv_kw': 8.9,
        'soc': 80,
        'forecast_morn': 'Green',
        'forecast_aftn': 'Amber',
        'forecast_eve': 'Red',
        'forecast_night': 'Red'
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'Morn' in data and 'mode' in data['Morn']
    assert 'NIGHT' in data and 'mode' in data['NIGHT']
    assert 'NightPrep' in data and 'mode' in data['NightPrep']

def test_simulate_missing_soc(client):
    resp = post_simulate(client, {
        'inverter_kw': 5.5,
        'battery_kwh': 24,
        'solar_pv_kw': 8.9,
        # 'soc' omitted
        'forecast_morn': 'Green',
        'forecast_aftn': 'Amber',
        'forecast_eve': 'Red',
        'forecast_night': 'Red'
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'Morn' in data and 'mode' in data['Morn']
    assert 'NIGHT' in data and 'mode' in data['NIGHT']

def test_simulate_empty_soc(client):
    resp = post_simulate(client, {
        'inverter_kw': 5.5,
        'battery_kwh': 24,
        'solar_pv_kw': 8.9,
        'soc': '',
        'forecast_morn': 'Green',
        'forecast_aftn': 'Amber',
        'forecast_eve': 'Red',
        'forecast_night': 'Red'
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'Morn' in data and 'mode' in data['Morn']
    assert 'NIGHT' in data and 'mode' in data['NIGHT']

def test_simulate_invalid_soc(client):
    resp = post_simulate(client, {
        'inverter_kw': 5.5,
        'battery_kwh': 24,
        'solar_pv_kw': 8.9,
        'soc': 'notanumber',
        'forecast_morn': 'Green',
        'forecast_aftn': 'Amber',
        'forecast_eve': 'Red',
        'forecast_night': 'Red'
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'Morn' in data and 'mode' in data['Morn']
    assert 'NIGHT' in data and 'mode' in data['NIGHT']
