"""
================================================================
Project : Time-Series Forecasting for Urban Air Quality
File    : app.py  (Python Flask Backend)
Pipeline: Arduino → ThingSpeak → [THIS FILE] → HTML Dashboard

INSTALL DEPENDENCIES:
  pip install flask flask-cors requests pandas statsmodels numpy

RUN:
  python app.py
  → Server starts at http://localhost:5000

THINGSPEAK CONFIG (edit the section below):
  CHANNEL_ID  : Your ThingSpeak Channel ID
  READ_API_KEY: Your ThingSpeak READ API Key
================================================================
"""

from flask import Flask, jsonify, render_template_string
from flask_cors import CORS
import requests
import pandas as pd
import numpy as np
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.stattools import adfuller
import warnings
import json
from datetime import datetime, timedelta
import logging

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # Allow HTML dashboard to call this API

# ── THINGSPEAK CONFIG ─────────────────────────────────────────
CHANNEL_ID   = "3304429"        # ← change
READ_API_KEY = "314SZA23O1570Z6R"      # ← change
TS_BASE_URL  = "https://api.thingspeak.com"

FIELD_MAP = {
    "field1": "aqi",
    "field2": "raw_ppm",
    "field3": "temperature",
    "field4": "humidity",
    "field5": "co2"
}

# AQI category thresholds
AQI_CATEGORIES = [
    (0,   50,  "Good",                  "#00e400"),
    (51,  100, "Moderate",              "#ffff00"),
    (101, 150, "Unhealthy for Sensitive","#ff7e00"),
    (151, 200, "Unhealthy",             "#ff0000"),
    (201, 300, "Very Unhealthy",        "#8f3f97"),
    (301, 500, "Hazardous",             "#7e0023"),
]

def get_aqi_category(aqi_value):
    """Return category label and color for a given AQI value."""
    for lo, hi, label, color in AQI_CATEGORIES:
        if lo <= aqi_value <= hi:
            return label, color
    return "Hazardous", "#7e0023"


def fetch_thingspeak(results=100):
    """Fetch latest N entries from ThingSpeak channel."""
    url = f"{TS_BASE_URL}/channels/{CHANNEL_ID}/feeds.json"
    params = {
        "api_key": READ_API_KEY,
        "results":  results
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        feeds = data.get("feeds", [])
        if not feeds:
            return None, "No data in ThingSpeak channel"

        rows = []
        for f in feeds:
            row = {"timestamp": f.get("created_at")}
            for field_key, col_name in FIELD_MAP.items():
                val = f.get(field_key)
                row[col_name] = float(val) if val not in (None, "") else np.nan
            rows.append(row)

        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        df.dropna(subset=["aqi"], inplace=True)
        return df, None

    except requests.exceptions.RequestException as e:
        logger.error(f"ThingSpeak fetch error: {e}")
        return None, str(e)


def run_arima_forecast(series, steps=12, field_name="value"):
    """
    Run ARIMA forecast on a pandas Series.
    Auto-selects differencing order d based on ADF test.
    Returns dict with forecast values, confidence intervals.
    """
    series = series.dropna()
    if len(series) < 10:
        return None, "Not enough data for ARIMA (need at least 10 points)"

    try:
        # Determine differencing order via ADF test
        adf_result = adfuller(series)
        d = 0 if adf_result[1] < 0.05 else 1  # p-value < 0.05 → stationary

        # Fit ARIMA(2, d, 2) — robust general config
        model = ARIMA(series, order=(2, d, 2))
        fitted = model.fit()

        forecast_obj = fitted.get_forecast(steps=steps)
        mean_fc      = forecast_obj.predicted_mean
        conf_int     = forecast_obj.conf_int(alpha=0.2)  # 80% CI

        last_ts = series.index[-1]
        # Infer interval between readings
        if len(series) > 1:
            avg_gap = (series.index[-1] - series.index[0]) / (len(series) - 1)
        else:
            avg_gap = timedelta(minutes=1)

        forecast_timestamps = [
            (last_ts + avg_gap * (i + 1)).isoformat()
            for i in range(steps)
        ]

        return {
            "timestamps":   forecast_timestamps,
            "forecast":     [round(float(v), 2) for v in mean_fc],
            "lower_bound":  [round(float(v), 2) for v in conf_int.iloc[:, 0]],
            "upper_bound":  [round(float(v), 2) for v in conf_int.iloc[:, 1]],
            "field":        field_name,
            "aic":          round(fitted.aic, 2),
            "model_order":  f"ARIMA(2,{d},2)"
        }, None

    except Exception as e:
        logger.error(f"ARIMA error for {field_name}: {e}")
        return None, str(e)


# ═══════════════════════════════════════════════════════════════
#  API ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/latest", methods=["GET"])
def api_latest():
    """Return the single most recent sensor reading."""
    df, err = fetch_thingspeak(results=1)
    if err:
        return jsonify({"error": err}), 500

    latest = df.iloc[-1]
    aqi_val = latest.get("aqi", 0)
    category, color = get_aqi_category(aqi_val)

    return jsonify({
        "timestamp":   df.index[-1].isoformat(),
        "aqi":         round(float(aqi_val), 1),
        "raw_ppm":     round(float(latest.get("raw_ppm", 0)), 0),
        "temperature": round(float(latest.get("temperature", 0)), 1),
        "humidity":    round(float(latest.get("humidity", 0)), 1),
        "co2":         round(float(latest.get("co2", 0)), 1),
        "aqi_category":category,
        "aqi_color":   color
    })


@app.route("/api/history", methods=["GET"])
def api_history():
    """Return last 80 readings for trend charts."""
    df, err = fetch_thingspeak(results=80)
    if err:
        return jsonify({"error": err}), 500

    result = {
        "timestamps":   [ts.isoformat() for ts in df.index],
        "aqi":          [round(float(v), 1) if not np.isnan(v) else None for v in df["aqi"]],
        "temperature":  [round(float(v), 1) if not np.isnan(v) else None for v in df["temperature"]],
        "humidity":     [round(float(v), 1) if not np.isnan(v) else None for v in df["humidity"]],
        "co2":          [round(float(v), 1) if not np.isnan(v) else None for v in df["co2"]],
        "count":        len(df)
    }
    return jsonify(result)


@app.route("/api/forecast", methods=["GET"])
def api_forecast():
    """Run ARIMA forecast for AQI and CO2, return 12-step predictions."""
    df, err = fetch_thingspeak(results=100)
    if err:
        return jsonify({"error": err}), 500

    forecasts = {}

    # Forecast AQI
    fc_aqi, err_aqi = run_arima_forecast(df["aqi"], steps=12, field_name="aqi")
    if fc_aqi:
        forecasts["aqi"] = fc_aqi
    else:
        forecasts["aqi"] = {"error": err_aqi}

    # Forecast CO2
    fc_co2, err_co2 = run_arima_forecast(df["co2"].dropna(), steps=12, field_name="co2")
    if fc_co2:
        forecasts["co2"] = fc_co2
    else:
        forecasts["co2"] = {"error": err_co2}

    # Forecast Temperature
    fc_temp, err_temp = run_arima_forecast(df["temperature"].dropna(), steps=12, field_name="temperature")
    if fc_temp:
        forecasts["temperature"] = fc_temp
    else:
        forecasts["temperature"] = {"error": err_temp}

    # Spike alert: check if any forecast AQI > 150
    spike_alert = False
    spike_time  = None
    if fc_aqi and "forecast" in fc_aqi:
        for i, val in enumerate(fc_aqi["forecast"]):
            if val > 150:
                spike_alert = True
                spike_time  = fc_aqi["timestamps"][i]
                break

    return jsonify({
        "forecasts":   forecasts,
        "spike_alert": spike_alert,
        "spike_time":  spike_time,
        "generated_at": datetime.utcnow().isoformat() + "Z"
    })


@app.route("/api/stats", methods=["GET"])
def api_stats():
    """Return descriptive statistics for dashboard summary cards."""
    df, err = fetch_thingspeak(results=100)
    if err:
        return jsonify({"error": err}), 500

    stats = {}
    for col in ["aqi", "temperature", "humidity", "co2"]:
        s = df[col].dropna()
        if len(s) > 0:
            stats[col] = {
                "mean":  round(float(s.mean()), 2),
                "max":   round(float(s.max()), 2),
                "min":   round(float(s.min()), 2),
                "std":   round(float(s.std()), 2),
                "count": len(s)
            }

    # Danger hours: count readings with AQI > 100
    danger_count = int((df["aqi"].dropna() > 100).sum())
    stats["danger_readings"] = danger_count
    stats["total_readings"]  = len(df)
    stats["danger_pct"]      = round(danger_count / max(len(df), 1) * 100, 1)

    return jsonify(stats)


@app.route("/api/health", methods=["GET"])
def api_health():
    """Health check endpoint."""
    return jsonify({
        "status":    "online",
        "service":   "Urban Air Quality API",
        "timestamp": datetime.utcnow().isoformat() + "Z"
    })


@app.route("/", methods=["GET"])
def index():
    """Serve a simple redirect info page."""
    return jsonify({
        "message": "Urban Air Quality API is running",
        "endpoints": [
            "/api/latest   — latest sensor reading",
            "/api/history  — historical data (last 80 readings)",
            "/api/forecast — ARIMA forecast (12 steps ahead)",
            "/api/stats    — descriptive statistics",
            "/api/health   — server health check"
        ],
        "dashboard": "Open index.html in your browser"
    })


if __name__ == "__main__":
    print("=" * 55)
    print("  Urban Air Quality Forecasting — Flask Backend")
    print("=" * 55)
    print(f"  ThingSpeak Channel : {CHANNEL_ID}")
    print(f"  API running at     : http://localhost:5000")
    print(f"  Open dashboard     : index.html in browser")
    print("=" * 55)
    app.run(debug=True, host="0.0.0.0", port=5000)