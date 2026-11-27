import hashlib
import json
import math
import os
import re
import time
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from dotenv import load_dotenv
from fpdf import FPDF
from langchain_core.tools import tool
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import InMemoryVectorStore
from deepagents import create_deep_agent

load_dotenv()

REPORTS_DIR = Path("reports")
REPORTS_DIR.mkdir(exist_ok=True)
VECTOR_STORE_FILE = Path("vector_reports_store.json")

PAKISTAN_MAJOR_CITIES = [
    "Lahore",
    "Karachi",
    "Islamabad",
    "Peshawar",
    "Quetta",
    "Multan",
]

STORM_CODES = {95, 96, 99}
RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}

WMO_LABELS = {
    0: "Clear",
    1: "Mainly Clear",
    2: "Partly Cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime Fog",
    51: "Light Drizzle",
    53: "Moderate Drizzle",
    55: "Dense Drizzle",
    61: "Slight Rain",
    63: "Moderate Rain",
    65: "Heavy Rain",
    80: "Rain Showers",
    81: "Moderate Showers",
    82: "Violent Showers",
    95: "Thunderstorm",
    96: "Thunderstorm + Hail",
    99: "Heavy Thunderstorm + Hail",
}

DEFAULT_FALLBACK_MODELS = [
    "groq:openai/gpt-oss-120b",
]

# (city, hours) the agent actually called its weather tool with. When set, it replaces the
# keyword guessing in _extract_city_and_hours, so any city worldwide is handled correctly.
_CITY_HINT: ContextVar = ContextVar("city_hint", default=None)


class SimpleFreeEmbeddings(Embeddings):
    """Zero-cost local embeddings combining keyword buckets and text digest."""

    VOCAB = [
        "rain", "storm", "thunder", "wind", "gust", "humidity", "pressure",
        "aqi", "air", "temperature", "temp", "heat", "cold", "clear", "cloudy",
        "forecast", "hourly", "flood", "alert", "risk", "high", "low", "moderate",
        "lahore", "karachi", "islamabad", "peshawar", "quetta", "multan", "istanbul",
    ]

    def __init__(self, size: int = 128):
        self.size = max(len(self.VOCAB) + 16, size)

    def _embed_text(self, text: str) -> list[float]:
        lower = text.lower()
        tokens = re.findall(r"[a-z0-9_]+", lower)
        vec = [0.0] * self.size

        for idx, word in enumerate(self.VOCAB):
            count = lower.count(word)
            if count:
                vec[idx] = min(1.0, 0.25 * count)

        for tok in tokens:
            h = hashlib.md5(tok.encode("utf-8")).digest()
            slot = len(self.VOCAB) + (h[0] % (self.size - len(self.VOCAB)))
            vec[slot] += 0.2

        digest = hashlib.sha256(lower.encode("utf-8")).digest()
        for i, b in enumerate(digest):
            vec[(i * 3) % self.size] += (b / 255.0) * 0.1

        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [round(x / norm, 6) for x in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_text(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_text(text)


_embeddings = SimpleFreeEmbeddings(size=128)
_vector_store = InMemoryVectorStore(embedding=_embeddings)


def _load_vectors() -> None:
    if not VECTOR_STORE_FILE.exists():
        return
    try:
        raw = json.loads(VECTOR_STORE_FILE.read_text(encoding="utf-8"))
        docs = [
            Document(page_content=item["content"], metadata=item.get("metadata", {}))
            for item in raw
            if item.get("content")
        ]
        if docs:
            _vector_store.add_documents(docs)
    except Exception:
        pass


def _save_vector(content: str, metadata: dict) -> None:
    items = []
    if VECTOR_STORE_FILE.exists():
        try:
            items = json.loads(VECTOR_STORE_FILE.read_text(encoding="utf-8"))
        except Exception:
            items = []
    items.append({"content": content, "metadata": metadata})
    VECTOR_STORE_FILE.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    _vector_store.add_documents([Document(page_content=content, metadata=metadata)])


_load_vectors()


def _safe_slug(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip().lower())
    return cleaned.strip("_") or "weather_brief"


def _format_slot_time(iso_str: str | None) -> str:
    if not iso_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(str(iso_str))
        return dt.strftime("%d %b %H:%M")
    except ValueError:
        return str(iso_str).replace("T", " ")


def _wind_dir_text(deg) -> str:
    if deg is None:
        return "N/A"
    try:
        val = float(deg) % 360.0
    except (TypeError, ValueError):
        return "N/A"
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    label = dirs[int((val + 22.5) // 45) % 8]
    return f"{label} ({round(val, 1)}°)"


def _clean_wind_cell(wind_val, wind_dir_str: str) -> str:
    clean_dir = re.sub(r"\(\(([^()]+)\)\)", r"(\1)", str(wind_dir_str or "").strip())
    return f"{wind_val} {clean_dir}".strip()


def _c_to_f(c):
    if c is None:
        return None
    try:
        return round((float(c) * 9.0 / 5.0) + 32.0, 1)
    except (TypeError, ValueError):
        return None


def _wmo_desc(code) -> str:
    if code is None:
        return "Unknown"
    try:
        c = int(code)
    except (TypeError, ValueError):
        return str(code)
    return WMO_LABELS.get(c, f"WMO {c}")


def _request_json_with_retry(url: str, params: dict, timeout: int = 12, retries: int = 3) -> dict:
    last_exc = None
    for attempt in range(retries):
        try:
            res = requests.get(url, params=params, timeout=timeout)
            if res.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(0.8 * (attempt + 1))
                continue
            res.raise_for_status()
            return res.json()
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise last_exc
    raise last_exc


def _hourly_storm_risk(code, gust, rain_prob, precip) -> tuple[str, str]:
    c_int = None
    if code is not None:
        try:
            c_int = int(code)
        except (TypeError, ValueError):
            c_int = None

    g_val = 0.0
    if gust is not None:
        try:
            g_val = float(gust)
        except (TypeError, ValueError):
            g_val = 0.0

    p_val = 0.0
    if precip is not None:
        try:
            p_val = float(precip)
        except (TypeError, ValueError):
            p_val = 0.0

    rp_val = 0.0
    if rain_prob is not None:
        try:
            rp_val = float(rain_prob)
        except (TypeError, ValueError):
            rp_val = 0.0

    has_thunder = c_int in STORM_CODES if c_int is not None else False
    if has_thunder or g_val >= 50.0 or (p_val >= 8.0 and g_val >= 30.0):
        return "High", "Yes"
    if g_val >= 35.0 or (p_val >= 3.0 and g_val >= 22.0) or (rp_val >= 70.0 and g_val >= 25.0):
        return "Moderate", "Yes" if g_val >= 40.0 else "No"
    if g_val >= 25.0 or (rp_val >= 50.0 and p_val > 0.0):
        return "Low", "No"
    return "None", "No"


def _evaluate_rain_and_storm(
    curr_code,
    hourly_codes: list,
    hourly_probs: list,
    hourly_precips: list,
    curr_gust,
    hourly_gusts: list,
    curr_pressure,
) -> dict:
    valid_probs = [float(p) for p in hourly_probs if p is not None]
    valid_precips = [float(x) for x in hourly_precips if x is not None]
    valid_gusts = [float(g) for g in hourly_gusts if g is not None]
    if curr_gust is not None:
        try:
            valid_gusts.append(float(curr_gust))
        except (TypeError, ValueError):
            pass

    max_prob = max(valid_probs) if valid_probs else 0.0
    total_precip = round(sum(valid_precips), 2) if valid_precips else 0.0
    max_gust = round(max(valid_gusts), 1) if valid_gusts else 0.0

    code_list = []
    for c in ([curr_code] + list(hourly_codes)):
        if c is not None:
            try:
                code_list.append(int(c))
            except (TypeError, ValueError):
                pass

    has_thunder_code = any(c in STORM_CODES for c in code_list)
    has_rain_code = has_thunder_code or any(c in RAIN_CODES for c in code_list)
    rain_signal = bool(has_rain_code or total_precip > 0.0 or max_prob >= 40.0)

    storm_reasons = []
    storm_score = 0

    if has_thunder_code:
        storm_score += 3
        storm_reasons.append("thunderstorm WMO code detected")
    if max_gust >= 50.0:
        storm_score += 3
        storm_reasons.append(f"damaging gusts up to {max_gust} km/h")
    elif max_gust >= 35.0:
        storm_score += 2
        storm_reasons.append(f"strong gusts up to {max_gust} km/h")
    elif max_gust >= 25.0:
        storm_score += 1
        storm_reasons.append(f"moderate gusts up to {max_gust} km/h")

    if total_precip >= 10.0 or (rain_signal and max_prob >= 75.0 and max_gust >= 25.0):
        storm_score += 2
        storm_reasons.append(f"heavy rain potential ({total_precip} mm, {max_prob}% prob)")
    elif rain_signal and max_prob >= 50.0:
        storm_score += 1
        storm_reasons.append(f"notable rain chance ({max_prob}%)")

    if curr_pressure is not None:
        try:
            if float(curr_pressure) < 1002.0 and rain_signal:
                storm_score += 1
                storm_reasons.append(f"low pressure ({curr_pressure} hPa)")
        except (TypeError, ValueError):
            pass

    if storm_score >= 4 or has_thunder_code:
        storm_risk = "High"
        storm_signal = True
    elif storm_score >= 2:
        storm_risk = "Moderate"
        storm_signal = max_gust >= 40.0
    elif storm_score == 1:
        storm_risk = "Low"
        storm_signal = False
    else:
        storm_risk = "None"
        storm_signal = False

    if not storm_reasons:
        storm_reason = "No thunderstorm codes, heavy rain, or strong wind gusts in window."
    else:
        storm_reason = "; ".join(storm_reasons)

    return {
        "max_rain_prob_pct": max_prob,
        "total_precip_mm": total_precip,
        "max_gust_kmh": max_gust,
        "rain_signal": rain_signal,
        "storm_signal": storm_signal,
        "storm_risk": storm_risk,
        "storm_reason": storm_reason,
    }


def _find_forecast_start_index(all_times: list[str], curr_time: str | None) -> int:
    if not all_times:
        return 0
    if curr_time:
        curr_str = str(curr_time)[:16]
        for idx, slot in enumerate(all_times):
            if str(slot)[:16] > curr_str:
                return idx
        if curr_str in [str(t)[:16] for t in all_times]:
            idx = [str(t)[:16] for t in all_times].index(curr_str)
            return idx + 1 if idx + 1 < len(all_times) else idx
    return 0


def _fetch_city_weather(city: str, hours: int = 6, include_hourly: bool = True) -> dict:
    hours = max(1, min(int(hours), 168))
    geo_data = _request_json_with_retry(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city, "count": 1, "language": "en", "format": "json"},
        timeout=12,
        retries=3,
    )
    results = geo_data.get("results") or []
    if not results:
        return {"city": city, "error": f"City '{city}' not found."}

    loc = results[0]
    lat, lon = loc["latitude"], loc["longitude"]
    tz = loc.get("timezone", "auto")
    display_name = f"{loc.get('name', city)}, {loc.get('country', '')}".strip(", ")

    w = _request_json_with_retry(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "timezone": tz,
            "current": (
                "temperature_2m,relative_humidity_2m,precipitation,rain,showers,"
                "weather_code,cloud_cover,pressure_msl,wind_speed_10m,"
                "wind_direction_10m,wind_gusts_10m"
            ),
            "hourly": (
                "temperature_2m,relative_humidity_2m,precipitation_probability,"
                "precipitation,rain,showers,weather_code,wind_speed_10m,"
                "wind_direction_10m,wind_gusts_10m"
            ),
            "forecast_days": 7,
        },
        timeout=12,
        retries=3,
    )

    aqi_val = "N/A"
    try:
        aqi_json = _request_json_with_retry(
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            params={"latitude": lat, "longitude": lon, "current": "us_aqi"},
            timeout=10,
            retries=2,
        )
        v = (aqi_json.get("current") or {}).get("us_aqi")
        if v is not None:
            aqi_val = v
    except Exception:
        aqi_val = "N/A"

    curr = w.get("current", {})
    hourly = w.get("hourly", {})
    curr_time = curr.get("time")

    # Exact local time of THIS city right now: UTC now + the city's UTC offset.
    # (Open-Meteo's current.time is only the 15-minute observation slot, so it lags behind.)
    utc_offset = w.get("utc_offset_seconds")
    exact_now_str = None
    if isinstance(utc_offset, (int, float)):
        exact_now_str = (datetime.now(timezone.utc) + timedelta(seconds=utc_offset)).strftime("%d %b %H:%M")

    all_times = hourly.get("time", [])
    start_idx = _find_forecast_start_index(all_times, curr_time)

    end_idx = start_idx + hours
    times = all_times[start_idx:end_idx]
    probs = hourly.get("precipitation_probability", [])[start_idx:end_idx]
    precips = hourly.get("precipitation", [])[start_idx:end_idx]
    codes = hourly.get("weather_code", [])[start_idx:end_idx]
    temps = hourly.get("temperature_2m", [])[start_idx:end_idx]
    humidities = hourly.get("relative_humidity_2m", [])[start_idx:end_idx]
    winds = hourly.get("wind_speed_10m", [])[start_idx:end_idx]
    wdirs = hourly.get("wind_direction_10m", [])[start_idx:end_idx]
    gusts = hourly.get("wind_gusts_10m", [])[start_idx:end_idx]

    curr_code = curr.get("weather_code")
    curr_gust = curr.get("wind_gusts_10m")
    curr_pressure = curr.get("pressure_msl")
    curr_humidity = curr.get("relative_humidity_2m")
    curr_wind = curr.get("wind_speed_10m")
    curr_wdir = curr.get("wind_direction_10m")

    eval_metrics = _evaluate_rain_and_storm(
        curr_code=curr_code,
        hourly_codes=codes,
        hourly_probs=probs,
        hourly_precips=precips,
        curr_gust=curr_gust,
        hourly_gusts=gusts,
        curr_pressure=curr_pressure,
    )

    temp_c = curr.get("temperature_2m")
    payload = {
        "city": display_name,
        "local_time": exact_now_str or _format_slot_time(curr_time),
        "observed_at": _format_slot_time(curr_time),
        "raw_local_time": curr_time,
        "utc_offset_seconds": utc_offset,
        "timezone": w.get("timezone", tz),
        "first_forecast_slot": _format_slot_time(times[0]) if times else "N/A",
        "forecast_window_hours": hours,
        "temp_c": temp_c,
        "temp_f": _c_to_f(temp_c),
        "humidity_pct": curr_humidity if curr_humidity is not None else "N/A",
        "wind_speed_kmh": curr_wind if curr_wind is not None else 0.0,
        "wind_direction": _wind_dir_text(curr_wdir),
        "wind_gusts_kmh": curr_gust if curr_gust is not None else 0.0,
        "pressure_hpa": curr_pressure if curr_pressure is not None else "N/A",
        "cloud_cover_pct": curr.get("cloud_cover", "N/A"),
        "aqi": aqi_val,
        "weather_code": curr_code,
        "weather_condition": _wmo_desc(curr_code),
        "max_rain_prob_pct": eval_metrics["max_rain_prob_pct"],
        "total_precip_mm": eval_metrics["total_precip_mm"],
        "max_gust_kmh": eval_metrics["max_gust_kmh"],
        "rain_signal": eval_metrics["rain_signal"],
        "storm_signal": eval_metrics["storm_signal"],
        "storm_risk": eval_metrics["storm_risk"],
        "storm_reason": eval_metrics["storm_reason"],
    }

    if include_hourly:
        if len(times) <= 12:
            step = 1
        elif len(times) <= 48:
            step = 6
        else:
            step = 24
        sampled_indices = list(range(0, len(times), step))
        payload["sample_step_hours"] = step
        rows = []
        for i in sampled_indices:
            h_code = codes[i] if i < len(codes) else None
            h_gust = gusts[i] if i < len(gusts) else None
            h_prob = probs[i] if i < len(probs) else None
            h_precip = precips[i] if i < len(precips) else None
            h_hum = humidities[i] if i < len(humidities) else None
            h_wind = winds[i] if i < len(winds) else None
            h_wdir = wdirs[i] if i < len(wdirs) else None
            s_risk, s_flag = _hourly_storm_risk(h_code, h_gust, h_prob, h_precip)
            rows.append({
                "time": _format_slot_time(times[i]),
                "raw_time": times[i],
                "temp_c": temps[i] if i < len(temps) else None,
                "temp_f": _c_to_f(temps[i]) if i < len(temps) else None,
                "humidity_pct": h_hum if h_hum is not None else "N/A",
                "wind_kmh": h_wind if h_wind is not None else 0.0,
                "wind_dir": _wind_dir_text(h_wdir),
                "gust_kmh": h_gust if h_gust is not None else 0.0,
                "rain_prob_pct": h_prob if h_prob is not None else 0,
                "precip_mm": h_precip if h_precip is not None else 0.0,
                "storm_risk": s_risk,
                "storm_flag": s_flag,
                "code": h_code,
                "condition": _wmo_desc(h_code),
            })
        payload["next_hours"] = rows
    return payload


def _extract_city_and_hours(query: str) -> tuple[str, int]:
    hint = _CITY_HINT.get()
    if hint:
        return hint

    q_low = query.lower()
    city_map = {
        "india": "New Delhi",
        "delhi": "New Delhi",
        "new delhi": "New Delhi",
        "mumbai": "Mumbai",
        "bangalore": "Bengaluru",
        "bengaluru": "Bengaluru",
        "chennai": "Chennai",
        "kolkata": "Kolkata",
        "hyderabad": "Hyderabad",
        "istanbul": "Istanbul",
    }
    city = None
    for key, val in city_map.items():
        if re.search(rf"\b{re.escape(key)}\b", q_low):
            city = val
            break

    if not city:
        for c in PAKISTAN_MAJOR_CITIES + ["London", "Dubai", "New York", "Tokyo", "Istanbul"]:
            if re.search(rf"\b{re.escape(c.lower())}\b", q_low):
                city = c
                break

    if not city:
        words = re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", query)
        skip = {
            "Weather", "Rain", "Storm", "Forecast", "Next", "Hours", "Days",
            "India", "Pakistan", "Istanbul",
        }
        for w in words:
            if w not in skip:
                city = w
                break

    city = city or "Lahore"

    hours = 12 if "live" in q_low else 6
    day_match = re.search(r"(\d+)\s*(?:din|dino|dinon|day|days)", q_low)
    hour_match = re.search(r"(\d+)\s*(?:ghant|ghanta|ghante|ghanton|hr|hrs|hour|hours)", q_low)
    if day_match:
        hours = max(24, min(int(day_match.group(1)) * 24, 168))
    elif hour_match:
        hours = max(1, min(int(hour_match.group(1)), 168))
    return city, hours


def _cities_from_messages(messages) -> list[tuple[str, int]]:
    """Read the cities the agent really asked the weather tools about (any city worldwide)."""
    found: list[tuple[str, int]] = []
    seen: set[str] = set()

    def add(city, hours) -> None:
        name = str(city or "").strip()
        if not name or name.lower() in seen:
            return
        try:
            hrs = max(1, min(int(hours), 168))
        except (TypeError, ValueError):
            hrs = 6
        seen.add(name.lower())
        found.append((name, hrs))

    for msg in messages or []:
        calls = msg.get("tool_calls") if isinstance(msg, dict) else getattr(msg, "tool_calls", None)
        for call in calls or []:
            name = call.get("name") if isinstance(call, dict) else getattr(call, "name", "")
            args = (call.get("args") if isinstance(call, dict) else getattr(call, "args", {})) or {}
            if name == "get_city_live_weather":
                add(args.get("city"), args.get("hours", 6))
            elif name == "compare_cities_for_reasoning":
                for c in (args.get("cities") or PAKISTAN_MAJOR_CITIES):
                    add(c, args.get("hours", 3))
    return found


def _live_conditions_banner(data: dict) -> str:
    return (
        f"> **Live Weather ({data.get('local_time')} — {data.get('city')}):** "
        f"**{data.get('weather_condition')}** | Temp: **{data.get('temp_c')}°C** ({data.get('temp_f')}°F) | "
        f"Humidity: **{data.get('humidity_pct')}%** | Wind: **{_clean_wind_cell(data.get('wind_speed_kmh'), data.get('wind_direction'))} km/h** | "
        f"Pressure: **{data.get('pressure_hpa')} hPa** | AQI: **{data.get('aqi')}**"
    )


def _build_upcoming_table_markdown(city: str, hours: int, include_live_banner: bool = True) -> tuple[str, dict]:
    data = _fetch_city_weather(city, hours=hours, include_hourly=True)
    if "error" in data:
        err_md = (
            f"\n\n- **Rain Prediction:** Unavailable ({data['error']})\n"
            "- **Storm Prediction:** Storm Signal: No | Storm Risk: None | Reason: City lookup failed."
        )
        return err_md, data

    rows = data.get("next_hours", [])
    table_lines = [f"\n\n### {data['city']} — Upcoming {hours}-Hour Forecast (From {data.get('first_forecast_slot')})"]
    if include_live_banner:
        table_lines.extend(["", _live_conditions_banner(data), ""])
    table_lines.extend([
        "| Time | Temp (°C) | Humidity % | Wind (km/h) | Gust (km/h) | Rain % | Precip mm | Storm Risk | Condition |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ])
    for r in rows:
        wind_str = _clean_wind_cell(r["wind_kmh"], r["wind_dir"])
        table_lines.append(
            f"| {r['time']} | {r['temp_c']} | {r['humidity_pct']} | {wind_str} | "
            f"{r['gust_kmh']} | {r['rain_prob_pct']} | {r['precip_mm']} | {r['storm_risk']} | {r['condition']} |"
        )
    return "\n".join(table_lines), data


def _build_fallback_table_and_summary(query: str) -> str:
    city, hours = _extract_city_and_hours(query)
    table_md, data = _build_upcoming_table_markdown(city, hours, include_live_banner=True)
    if "error" in data:
        return table_md

    rain_sig = "Yes" if data.get("rain_signal") else "No"
    storm_sig = "Yes" if data.get("storm_signal") else "No"
    summary_lines = [
        table_md,
        "",
        "### Rain & Storm Prediction Summary",
        f"- **Live Weather ({data.get('local_time')}):** {data.get('weather_condition')} | Temp: {data.get('temp_c')}°C ({data.get('temp_f')}°F) | Humidity: {data.get('humidity_pct')}% | Pressure: {data.get('pressure_hpa')} hPa | AQI: {data.get('aqi')}",
        f"- **Rain Prediction:** Rain Signal: {rain_sig} | Max Rain Probability: {data.get('max_rain_prob_pct')}% | Total Forecast Precip: {data.get('total_precip_mm')} mm",
        f"- **Storm Prediction:** Storm Signal: {storm_sig} | Storm Risk: {data.get('storm_risk')} | Max Gust: {data.get('max_gust_kmh')} km/h | Reason: {data.get('storm_reason')}",
    ]
    return "\n".join(summary_lines)


def _table_has_stale_past_slots(lines: list[str], start_idx: int, full_text: str = "") -> bool:
    """Detect if a markdown table starts with past or mismatched slots."""
    city, hours = _extract_city_and_hours(f"{full_text}\n" + "\n".join(lines))
    fresh = _fetch_city_weather(city, hours=hours, include_hourly=True)
    raw_curr = str(fresh.get("raw_local_time") or "")
    expected_first_slot = str((fresh.get("next_hours") or [{}])[0].get("time") or "")
    if "T" not in raw_curr:
        return False
    curr_date, curr_hm = raw_curr.split("T", 1)
    curr_hm = curr_hm[:5]

    j = start_idx + 2
    while j < len(lines) and "|" in lines[j] and not lines[j].strip().startswith("#"):
        cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
        if cells:
            first_col = cells[0]
            if expected_first_slot and first_col == expected_first_slot:
                return False

            m_full = re.search(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})", first_col)
            if m_full:
                slot_date, slot_hm = m_full.group(1), m_full.group(2)
                return slot_date < curr_date or (slot_date == curr_date and slot_hm < curr_hm)

            m_day = re.search(r"\b(\d{1,2}\s+[A-Za-z]{3})\s+(\d{2}:\d{2})\b", first_col)
            if m_day:
                try:
                    dt_curr = datetime.fromisoformat(raw_curr[:16])
                    dt_slot = datetime.strptime(f"{dt_curr.year} {m_day.group(1)} {m_day.group(2)}", "%Y %d %b %H:%M")
                    return dt_slot < dt_curr
                except ValueError:
                    return False

            m_hm = re.search(r"\b(\d{2}:\d{2})\b", first_col)
            if m_hm:
                slot_hm = m_hm.group(1)
                if curr_hm >= "12:00" and slot_hm < "12:00":
                    return True
                return False
        j += 1
    return False


def _row_storm_risk_from_cells(cells: list[str]) -> str:
    joined = " ".join(c.lower() for c in cells)
    for m in re.findall(r"(\d+(?:\.\d+)?)\s*km/h", joined):
        if float(m) >= 50.0:
            return "High"
        if float(m) >= 35.0:
            return "Moderate"
        if float(m) >= 25.0:
            return "Low"
    for code_num in ["95", "96", "99"]:
        if re.search(rf"\b{code_num}\b", joined):
            return "High"
    return "None"


def _enforce_storm_and_rain_output(text: str, query: str) -> str:
    lines = text.splitlines()
    out_lines = []
    has_markdown_table = False
    replaced_stale_table = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if (
            "|" in line
            and i + 1 < len(lines)
            and re.match(r"^\s*\|?\s*[:\-]+\s*(\|\s*[:\-]+\s*)+\|?\s*$", lines[i + 1])
        ):
            has_markdown_table = True
            if _table_has_stale_past_slots(lines, i, full_text=f"{query}\n{text}"):
                city, hours = _extract_city_and_hours(f"{query}\n{text}")
                fresh_md, _ = _build_upcoming_table_markdown(city, hours, include_live_banner=True)
                out_lines.append(fresh_md.strip())
                replaced_stale_table = True
                i += 2
                while i < len(lines) and "|" in lines[i] and not lines[i].strip().startswith("#"):
                    i += 1
                continue

            city, hours = _extract_city_and_hours(f"{query}\n{text}")
            live_data = _fetch_city_weather(city, hours=hours, include_hourly=False)
            if "error" not in live_data:
                banner = _live_conditions_banner(live_data)
                if banner not in "\n".join(out_lines):
                    out_lines.append("")
                    out_lines.append(banner)
                    out_lines.append("")

            header_cells = [c.strip() for c in line.strip().strip("|").split("|")]
            has_storm_col = any("storm" in c.lower() for c in header_cells)
            if not has_storm_col:
                out_lines.append("| " + " | ".join(header_cells + ["Storm Risk"]) + " |")
                sep_cells = [c.strip() for c in lines[i + 1].strip().strip("|").split("|")]
                out_lines.append("| " + " | ".join(sep_cells + ["---"]) + " |")
                i += 2
                while i < len(lines) and "|" in lines[i] and not lines[i].strip().startswith("#"):
                    row_cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                    if len(row_cells) >= 2:
                        row_cells = [re.sub(r"\(\(([^()]+)\)\)", r"(\1)", c) for c in row_cells]
                        s_risk = _row_storm_risk_from_cells(row_cells)
                        out_lines.append("| " + " | ".join(row_cells + [s_risk]) + " |")
                    else:
                        out_lines.append(lines[i])
                    i += 1
                continue

        out_lines.append(re.sub(r"\(\(([^()]+)\)\)", r"(\1)", line))
        i += 1

    updated = "\n".join(out_lines).strip()
    if not has_markdown_table:
        updated += _build_fallback_table_and_summary(query)
    elif replaced_stale_table:
        city, hours = _extract_city_and_hours(f"{query}\n{text}")
        fresh_data = _fetch_city_weather(city, hours=hours, include_hourly=False)
        if "error" not in fresh_data:
            rain_sig = "Yes" if fresh_data.get("rain_signal") else "No"
            storm_sig = "Yes" if fresh_data.get("storm_signal") else "No"
            updated += (
                "\n\n### Rain & Storm Prediction Summary\n"
                f"- **Live Weather ({fresh_data.get('local_time')}):** {fresh_data.get('weather_condition')} | Temp: {fresh_data.get('temp_c')}°C ({fresh_data.get('temp_f')}°F) | Humidity: {fresh_data.get('humidity_pct')}% | Pressure: {fresh_data.get('pressure_hpa')} hPa | AQI: {fresh_data.get('aqi')}\n"
                f"- **Rain Prediction:** Rain Signal: {rain_sig} | Max Rain Probability: {fresh_data.get('max_rain_prob_pct')}% | Total Forecast Precip: {fresh_data.get('total_precip_mm')} mm\n"
                f"- **Storm Prediction:** Storm Signal: {storm_sig} | Storm Risk: {fresh_data.get('storm_risk')} | Max Gust: {fresh_data.get('max_gust_kmh')} km/h | Reason: {fresh_data.get('storm_reason')}"
            )
    elif "storm prediction" not in updated.lower() and "storm risk" not in updated.lower():
        updated += (
            "\n\n### Rain & Storm Prediction Summary\n"
            "- **Rain Prediction:** Evaluated from live hourly precipitation probability and rainfall (mm) for the requested window.\n"
            "- **Storm Prediction:** Storm Signal: No | Storm Risk: None | Reason: No thunderstorm WMO codes (95–99), heavy rain, or damaging wind gusts detected in the forecast window."
        )
    return updated


def _clean_pdf_text(text: str) -> str:
    replacements = {
        "\u2014": " - ",
        "\u2013": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2022": "-",
        "\u00b0": " deg",
    }
    out = str(text or "")
    for src, dst in replacements.items():
        out = out.replace(src, dst)
    out = re.sub(r"\s{2,}", " ", out)
    return out.encode("latin-1", "replace").decode("latin-1").strip()


def _draw_cloud(pdf: FPDF, cx: float, cy: float, scale: float = 1.0, fill_rgb=(255, 255, 255)) -> None:
    """Draw a clean multi-puff cloud using circles and a base rectangle."""
    r, g, b = fill_rgb
    pdf.set_fill_color(r, g, b)
    puffs = [
        (-11 * scale, 1 * scale, 6.5 * scale),
        (-4 * scale, -3 * scale, 8.5 * scale),
        (5 * scale, -2 * scale, 7.5 * scale),
        (12 * scale, 1.5 * scale, 6.0 * scale),
    ]
    for dx, dy, rad in puffs:
        pdf.ellipse(cx + dx - rad, cy + dy - rad, rad * 2, rad * 2, style="F")
    base_w = 24 * scale
    base_h = 6.5 * scale
    pdf.rect(cx - 11 * scale, cy + 1.5 * scale, base_w, base_h, style="F")


@tool
def get_city_live_weather(city: str, hours: int = 6) -> str:
    """Get live weather, wind/humidity details, rain & storm prediction, and forecast (1 to 168 hours) for any city."""
    data = _fetch_city_weather(city, hours=hours, include_hourly=True)
    if "error" in data:
        return data["error"]
    return json.dumps(data, separators=(",", ":"))


@tool
def compare_cities_for_reasoning(cities: list[str] | None = None, hours: int = 3) -> str:
    """Fetch compact live weather, wind, humidity, rain, and storm indicators across multiple cities."""
    target_cities = (cities[:6] if cities else PAKISTAN_MAJOR_CITIES)
    hours = max(1, min(int(hours), 48))
    items = [_fetch_city_weather(c, hours=hours, include_hourly=False) for c in target_cities]
    return json.dumps({"horizon_hours": hours, "cities": items}, separators=(",", ":"))


@tool
def search_past_weather_reports(query: str, k: int = 3) -> str:
    """Search previously stored weather and storm reports in the vector store for historical comparison."""
    k = max(1, min(int(k), 5))
    try:
        docs = _vector_store.similarity_search(query, k=k)
    except Exception:
        docs = []
    if not docs:
        return "No matching past reports found in vector store."
    out = []
    for d in docs:
        out.append({
            "metadata": d.metadata,
            "summary": d.page_content[:350],
        })
    return json.dumps(out, separators=(",", ":"))


@tool
def generate_news_pdf_and_store_vector(
    report_prefix: str,
    headline: str,
    risk_level: str,
    news_feed_analysis: str,
) -> str:
    """Generate a newsroom PDF report in reports/ and save the analysis in the vector store. Returns the PDF filename."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{_safe_slug(report_prefix)}_{stamp}.pdf"
    filepath = REPORTS_DIR / filename

    clean_headline = _clean_pdf_text(headline)
    clean_risk = _clean_pdf_text(risk_level).upper()
    clean_body = _clean_pdf_text(news_feed_analysis)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    risk_colors = {
        "NONE": (22, 101, 52, 240, 253, 244),
        "LOW": (146, 64, 14, 255, 251, 235),
        "MODERATE": (154, 52, 18, 255, 247, 237),
        "HIGH": (153, 27, 27, 254, 242, 242),
    }
    fg_r, fg_g, fg_b, bg_r, bg_g, bg_b = risk_colors.get(
        clean_risk, (30, 58, 138, 239, 246, 255)
    )
    storm_cloud_rgb = (120, 140, 168) if clean_risk in {"MODERATE", "HIGH"} else (235, 244, 255)

    class NewsroomPDF(FPDF):
        def footer(self):
            self.set_y(-14)
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(110, 120, 135)
            self.cell(
                0,
                8,
                f"Weather & Storm Newsroom Desk  |  Generated {generated_at}  |  Page {self.page_no()}",
                align="C",
            )

    pdf = NewsroomPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    banner_y = pdf.get_y()
    pdf.set_fill_color(15, 36, 74)
    pdf.rect(pdf.l_margin, banner_y, pdf.w - pdf.l_margin - pdf.r_margin, 18, style="F")

    _draw_cloud(pdf, pdf.w - pdf.r_margin - 36, banner_y + 8.5, scale=0.68, fill_rgb=storm_cloud_rgb)
    _draw_cloud(pdf, pdf.w - pdf.r_margin - 15, banner_y + 10.5, scale=0.48, fill_rgb=(255, 255, 255))

    pdf.set_xy(pdf.l_margin + 3, banner_y + 2.5)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 13.5)
    pdf.cell(0, 6.5, "WEATHER ANALYSIS & STORM NEWS BRIEF", ln=True)

    pdf.set_xy(pdf.l_margin + 3, banner_y + 10)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(191, 219, 254)
    pdf.cell(0, 5, "LIVE METEOROLOGICAL & STORM RISK DESK", ln=True)

    pdf.set_y(banner_y + 22)

    pdf.set_text_color(15, 23, 42)
    pdf.set_font("Helvetica", "B", 14.5)
    pdf.multi_cell(0, 7.5, clean_headline)

    pdf.ln(3)

    pdf.set_fill_color(bg_r, bg_g, bg_b)
    pdf.set_draw_color(fg_r, fg_g, fg_b)
    pdf.set_text_color(fg_r, fg_g, fg_b)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(0, 8.5, f"  STORM RISK LEVEL: {clean_risk}    |    ISSUED: {generated_at}", border=1, ln=True, fill=True)

    pdf.ln(5)

    pdf.set_text_color(15, 36, 74)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "NEWSROOM BRIEF & FORECAST SUMMARY", ln=True)
    pdf.set_draw_color(203, 213, 225)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(3)

    pdf.set_text_color(30, 41, 59)
    pdf.set_font("Helvetica", "", 10.5)
    for para in [p.strip() for p in re.split(r"\n\s*\n", clean_body) if p.strip()]:
        pdf.multi_cell(0, 6, para)
        pdf.ln(2.5)

    pdf.ln(2)

    note_y = pdf.get_y()
    pdf.set_fill_color(241, 245, 249)
    pdf.set_text_color(71, 85, 105)
    pdf.set_font("Helvetica", "I", 8.8)
    pdf.multi_cell(
        0,
        5.5,
        "Editorial Advisory: Compiled from live meteorological, wind, precipitation, and air quality feeds. "
        "Confirm severe weather warnings with official national meteorological authorities before broadcast or travel.",
        fill=True,
    )
    if pdf.get_y() - note_y < 14:
        _draw_cloud(pdf, pdf.w - pdf.r_margin - 14, pdf.get_y() + 2, scale=0.35, fill_rgb=(219, 234, 254))

    pdf.output(str(filepath))

    _save_vector(
        content=f"Headline: {clean_headline}\nRisk: {clean_risk}\n{clean_body}",
        metadata={
            "pdf_filename": filename,
            "report_prefix": report_prefix,
            "risk_level": clean_risk.title(),
            "created_at": datetime.now().isoformat(),
        },
    )
    return filename


SYSTEM_PROMPT = """You are a Weather Analysis & Storm Prediction Deep Agent for news feeds and users worldwide.
Keep responses concise, exact, and within token limits.
Follow these rules:
1. For a single city's live weather, coming-hours forecast, or rain/storm prediction, call `get_city_live_weather` (pass `hours=3..12` for coming hours, `hours=48` for 2 days, or `hours=168` for 7 days).
2. For multi-city rain/storm predictions or comparisons, call `compare_cities_for_reasoning` with relevant cities (up to 6) and the requested hours.
3. Copy ONLY exact `time` values from `next_hours` into forecast rows (`time`, `temp_c`, `humidity_pct`, `wind_kmh` + `wind_dir`, `gust_kmh`, `rain_prob_pct`, `precip_mm`, `storm_risk`, `condition`). Never invent `~` ranges, and never repeat current AQI or Pressure inside hourly rows.
4. EVERY forecast table MUST include `Rain %`, `Precip mm`, and `Storm Risk` columns on every row, AND the response MUST include both **Rain Prediction** (`rain_signal`, `max_rain_prob_pct`, `total_precip_mm`) and **Storm Prediction** (`storm_signal`, `storm_risk`, `storm_reason`).
5. Always call `generate_news_pdf_and_store_vector` once with a clear `report_prefix`, news-ready headline, risk level (`None`, `Low`, `Moderate`, `High`), and concise news-feed analysis.
6. In your final reply, mention only the exact PDF filename returned by `generate_news_pdf_and_store_vector` (no folder path prefix).
"""


def _get_model_chain() -> list[str]:
    primary = os.getenv("WEATHER_AGENT_MODEL", "").strip()
    chain = []
    if primary:
        chain.append(primary)
    for model_name in DEFAULT_FALLBACK_MODELS:
        if model_name not in chain:
            chain.append(model_name)
    return chain


def _should_try_next_model(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "429" in msg
        or "rate_limit" in msg
        or "rate limit" in msg
        or "tokens per day" in msg
        or "tpd" in msg
        or "404" in msg
        or "model_not_found" in msg
        or "does not exist" in msg
        or "decommissioned" in msg
    )


def build_agent(model_name: str | None = None):
    chosen = model_name or _get_model_chain()[0]
    return create_deep_agent(
        model=chosen,
        tools=[
            get_city_live_weather,
            compare_cities_for_reasoning,
            search_past_weather_reports,
            generate_news_pdf_and_store_vector,
        ],
        system_prompt=SYSTEM_PROMPT,
    )


def _invoke_with_fallback(query: str):
    models = _get_model_chain()
    last_exc = None
    for idx, model_name in enumerate(models):
        try:
            current_agent = build_agent(model_name)
            return current_agent.invoke({"messages": [{"role": "user", "content": query}]})
        except Exception as exc:
            last_exc = exc
            has_next = idx < len(models) - 1
            if _should_try_next_model(exc) and has_next:
                continue
            raise
    raise last_exc


def run_weather_agent(query: str) -> dict:
    before_files = {p.name: p.stat().st_mtime_ns for p in REPORTS_DIR.glob("*.pdf")}

    result = _invoke_with_fallback(query)
    messages = result.get("messages", [])
    final_text = messages[-1].content if messages else "No response generated."
    if not isinstance(final_text, str):
        final_text = str(final_text)

    # The cities the agent really looked up (works for any city, not just the hard-coded ones).
    detected = _cities_from_messages(messages)

    hint_token = _CITY_HINT.set(detected[0]) if detected else None
    try:
        final_text = _enforce_storm_and_rain_output(final_text, query)
    finally:
        if hint_token is not None:
            _CITY_HINT.reset(hint_token)

    created_or_updated = [
        p
        for p in REPORTS_DIR.glob("*.pdf")
        if p.name not in before_files or p.stat().st_mtime_ns > before_files[p.name]
    ]
    created_or_updated.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)

    pdf_name = None
    match = re.search(r"([a-zA-Z0-9_-]+_\d{8}_\d{6}\.pdf)", final_text)
    if match and (REPORTS_DIR / match.group(1)).exists():
        pdf_name = match.group(1)
    elif created_or_updated:
        pdf_name = created_or_updated[0].name

    # Live conditions (with the exact local time of each city) for the card at the top of the UI.
    live = []
    for city_name, _hrs in detected[:3]:
        try:
            snap = _fetch_city_weather(city_name, hours=1, include_hourly=False)
        except Exception:
            continue
        if "error" not in snap:
            live.append(snap)

    return {
        "response": final_text,
        "pdf_filename": pdf_name,
        "live": live,
    }


def get_stored_reports() -> list[dict]:
    if not VECTOR_STORE_FILE.exists():
        return []
    try:
        return json.loads(VECTOR_STORE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def clear_all_reports() -> int:
    """Delete every saved report (PDFs + the vector store file) and reset the in-memory store.
    New analyses are saved again as usual afterwards. Returns how many PDFs were removed."""
    global _vector_store
    removed = 0
    for pdf in REPORTS_DIR.glob("*.pdf"):
        try:
            pdf.unlink()
            removed += 1
        except OSError:
            pass
    try:
        VECTOR_STORE_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        VECTOR_STORE_FILE.write_text("[]", encoding="utf-8")
    _vector_store = InMemoryVectorStore(embedding=_embeddings)
    return removed