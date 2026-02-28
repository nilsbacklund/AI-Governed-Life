import httpx

# WMO weather code → human description
_WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}


async def fetch_weather(lat: float, lon: float, forecast_hours: int = 12) -> dict:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation,relative_humidity_2m",
        "hourly": "temperature_2m,weather_code,precipitation_probability",
        "forecast_hours": forecast_hours,
        "timezone": "auto",
    }

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    current = data["current"]
    result = {
        "current": {
            "temperature_c": current["temperature_2m"],
            "feels_like_c": current["apparent_temperature"],
            "condition": _WMO_CODES.get(current["weather_code"], f"Code {current['weather_code']}"),
            "wind_kmh": current["wind_speed_10m"],
            "precipitation_mm": current["precipitation"],
            "humidity_pct": current["relative_humidity_2m"],
        },
        "forecast": [],
    }

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    codes = hourly.get("weather_code", [])
    rain_probs = hourly.get("precipitation_probability", [])

    for i in range(len(times)):
        t = times[i]
        hour_part = t.split("T")[1] if "T" in t else t
        result["forecast"].append({
            "time": hour_part,
            "temp_c": temps[i] if i < len(temps) else None,
            "condition": _WMO_CODES.get(codes[i], f"Code {codes[i]}") if i < len(codes) else "Unknown",
            "rain_chance_pct": rain_probs[i] if i < len(rain_probs) else None,
        })

    return result
