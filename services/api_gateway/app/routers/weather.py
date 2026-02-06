"""
Weather router for VOS API Gateway.

Provides simple HTTP endpoints for weather data that can be used by frontend applications.
"""

import logging
import os
import requests
from typing import Optional
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


class WeatherResponse(BaseModel):
    """Response schema for weather data"""
    location: str
    country: str
    temperature: dict
    condition: dict
    atmosphere: dict
    wind: dict
    visibility: str
    cloudiness: str
    sunrise: str
    sunset: str
    timestamp: str


class ForecastResponse(BaseModel):
    """Response schema for forecast data"""
    location: str
    country: str
    days_count: int
    daily_summaries: list
    units: dict
    timestamp: str


@router.get("/weather/current", response_model=WeatherResponse)
async def get_current_weather(
    location: str = Query(..., description="City name or location (e.g., 'San Francisco', 'London,UK')"),
    units: str = Query("metric", description="Temperature units: 'metric' (Celsius) or 'imperial' (Fahrenheit)")
):
    """
    Get current weather for a location.

    This is a simple REST endpoint that returns current weather data from OpenWeatherMap.

    Args:
        location: City name, optionally with country code (e.g., "London,UK")
        units: Temperature units - 'metric' for Celsius or 'imperial' for Fahrenheit

    Returns:
        Current weather data including temperature, conditions, humidity, wind, etc.

    Example:
        GET /api/v1/weather/current?location=San Francisco&units=metric
    """
    api_key = os.environ.get("OPENWEATHERMAP_API_KEY")

    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="Weather service not configured (missing API key)"
        )

    try:
        # Call OpenWeatherMap API
        params = {
            "q": location,
            "appid": api_key,
            "units": units
        }

        response = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params=params,
            timeout=10
        )

        if response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=f"Location '{location}' not found"
            )

        response.raise_for_status()
        data = response.json()

        # Format the response
        weather_data = {
            "location": data["name"],
            "country": data["sys"]["country"],
            "temperature": {
                "current": data["main"]["temp"],
                "feels_like": data["main"]["feels_like"],
                "min": data["main"]["temp_min"],
                "max": data["main"]["temp_max"],
                "unit": "°C" if units == "metric" else "°F"
            },
            "condition": {
                "main": data["weather"][0]["main"],
                "description": data["weather"][0]["description"].capitalize(),
                "icon": data["weather"][0]["icon"]
            },
            "atmosphere": {
                "humidity": f"{data['main']['humidity']}%",
                "pressure": f"{data['main']['pressure']} hPa"
            },
            "wind": {
                "speed": f"{data['wind']['speed']} {'m/s' if units == 'metric' else 'mph'}",
                "direction": data["wind"].get("deg", "N/A")
            },
            "visibility": f"{data.get('visibility', 10000) / 1000:.1f} km",
            "cloudiness": f"{data['clouds']['all']}%",
            "sunrise": datetime.fromtimestamp(data["sys"]["sunrise"], tz=timezone(timedelta(seconds=data["timezone"]))).strftime("%H:%M"),
            "sunset": datetime.fromtimestamp(data["sys"]["sunset"], tz=timezone(timedelta(seconds=data["timezone"]))).strftime("%H:%M"),
            "timestamp": datetime.now().isoformat()
        }

        logger.info(f"Weather data fetched for {location}: {data['weather'][0]['description']}, {data['main']['temp']}°")

        return weather_data

    except requests.exceptions.Timeout:
        raise HTTPException(
            status_code=504,
            detail="Weather service request timed out"
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Weather API error: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Weather service error: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Unexpected error fetching weather: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch weather data: {str(e)}"
        )


@router.get("/weather/forecast", response_model=ForecastResponse)
async def get_weather_forecast(
    location: str = Query(..., description="City name or location"),
    days: int = Query(5, ge=1, le=5, description="Number of days to forecast (1-5)"),
    units: str = Query("metric", description="Temperature units: 'metric' or 'imperial'")
):
    """
    Get weather forecast for a location.

    Returns a multi-day forecast with 3-hour intervals, aggregated into daily summaries.

    Args:
        location: City name, optionally with country code
        days: Number of days to forecast (1-5)
        units: Temperature units

    Returns:
        Forecast data with daily summaries

    Example:
        GET /api/v1/weather/forecast?location=New York&days=3&units=imperial
    """
    api_key = os.environ.get("OPENWEATHERMAP_API_KEY")

    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="Weather service not configured (missing API key)"
        )

    try:
        params = {
            "q": location,
            "appid": api_key,
            "units": units,
            "cnt": days * 8  # 8 forecasts per day (3-hour intervals)
        }

        response = requests.get(
            "https://api.openweathermap.org/data/2.5/forecast",
            params=params,
            timeout=10
        )

        if response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=f"Location '{location}' not found"
            )

        response.raise_for_status()
        data = response.json()

        # Get timezone offset from city data
        tz_offset = data["city"].get("timezone", 0)
        local_tz = timezone(timedelta(seconds=tz_offset))

        # Process forecast data into daily summaries
        daily_forecasts = {}

        for item in data["list"]:
            date = datetime.fromtimestamp(item["dt"], tz=local_tz).strftime("%Y-%m-%d")

            if date not in daily_forecasts:
                daily_forecasts[date] = {
                    "date": date,
                    "day_name": datetime.fromtimestamp(item["dt"], tz=local_tz).strftime("%A"),
                    "temperatures": [],
                    "conditions": [],
                    "descriptions": [],
                    "humidity_values": [],
                    "wind_speeds": [],
                    "rain_amounts": [],
                    "forecasts": []
                }

            # Collect data for aggregation
            daily_forecasts[date]["temperatures"].append(item["main"]["temp"])
            daily_forecasts[date]["conditions"].append(item["weather"][0]["main"])
            daily_forecasts[date]["descriptions"].append(item["weather"][0]["description"])
            daily_forecasts[date]["humidity_values"].append(item["main"]["humidity"])
            daily_forecasts[date]["wind_speeds"].append(item["wind"]["speed"])

            if "rain" in item:
                daily_forecasts[date]["rain_amounts"].append(item["rain"].get("3h", 0))

            # Add detailed forecast for this time slot
            daily_forecasts[date]["forecasts"].append({
                "time": datetime.fromtimestamp(item["dt"], tz=local_tz).strftime("%H:%M"),
                "temp": item["main"]["temp"],
                "condition": item["weather"][0]["main"],
                "description": item["weather"][0]["description"]
            })

        # Create summary for each day
        forecast_result = {
            "location": data["city"]["name"],
            "country": data["city"]["country"],
            "days_count": len(daily_forecasts),
            "daily_summaries": [],
            "units": {
                "temperature": "°C" if units == "metric" else "°F",
                "wind": "m/s" if units == "metric" else "mph"
            },
            "timestamp": datetime.now(tz=local_tz).isoformat()
        }

        for date, day_data in list(daily_forecasts.items())[:days]:
            temps = day_data["temperatures"]

            summary = {
                "date": date,
                "day_name": day_data["day_name"],
                "temperature": {
                    "high": max(temps),
                    "low": min(temps),
                    "average": sum(temps) / len(temps)
                },
                "condition": max(set(day_data["conditions"]), key=day_data["conditions"].count),
                "description": max(set(day_data["descriptions"]), key=day_data["descriptions"].count),
                "humidity": f"{sum(day_data['humidity_values']) // len(day_data['humidity_values'])}%",
                "wind_speed": f"{sum(day_data['wind_speeds']) / len(day_data['wind_speeds']):.1f}",
                "rain_total": f"{sum(day_data['rain_amounts']):.1f} mm" if day_data["rain_amounts"] else "0 mm",
                "detailed_forecasts": day_data["forecasts"]
            }

            forecast_result["daily_summaries"].append(summary)

        logger.info(f"Forecast data fetched for {location}: {days} days")

        return forecast_result

    except requests.exceptions.Timeout:
        raise HTTPException(
            status_code=504,
            detail="Weather service request timed out"
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Weather API error: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Weather service error: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Unexpected error fetching forecast: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch forecast data: {str(e)}"
        )
