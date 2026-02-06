"""
Weather-specific tools for the Weather Agent.

Uses OpenWeatherMap API for real weather data.
Requires OPENWEATHERMAP_API_KEY environment variable.
"""

import os
import logging
import requests
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta

from vos_sdk import BaseTool

logger = logging.getLogger(__name__)


class GetWeatherTool(BaseTool):
    """
    Fetches current weather data for a location using OpenWeatherMap API.
    """

    def __init__(self):
        super().__init__(
            name="get_weather",
            description="Fetches current weather data for a specified location"
        )
        self.api_key = os.environ.get("OPENWEATHERMAP_API_KEY")
        self.base_url = "https://api.openweathermap.org/data/2.5/weather"

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate that location is provided."""
        if "location" not in arguments:
            return False, "Missing required argument: 'location'"

        if not isinstance(arguments["location"], str):
            return False, f"'location' must be a string, got {type(arguments['location']).__name__}"

        if not arguments["location"].strip():
            return False, "'location' cannot be empty"

        if not self.api_key:
            return False, "OPENWEATHERMAP_API_KEY environment variable not set"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "get_weather",
            "description": "Fetches current weather data for a specified location",
            "parameters": [
                {
                    "name": "location",
                    "type": "str",
                    "description": "City name, country, or location",
                    "required": True
                },
                {
                    "name": "units",
                    "type": "str",
                    "description": "Temperature units - 'metric' (Celsius) or 'imperial' (Fahrenheit)",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Fetch weather data for the specified location.

        Args:
            arguments: Must contain 'location' key, optionally 'units' (metric/imperial)
        """
        location = arguments["location"]
        units = arguments.get("units", "metric")  # Default to Celsius

        try:
            params = {
                "q": location,
                "appid": self.api_key,
                "units": units
            }

            response = requests.get(self.base_url, params=params, timeout=10)

            if response.status_code == 404:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Location '{location}' not found"
                )
                return

            response.raise_for_status()
            data = response.json()

            # Parse and format the response
            weather_result = {
                "location": data["name"],
                "country": data["sys"]["country"],
                "coordinates": {
                    "lat": data["coord"]["lat"],
                    "lon": data["coord"]["lon"]
                },
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
                "timezone": data["timezone"],
                "sunrise": datetime.fromtimestamp(data["sys"]["sunrise"], tz=timezone(timedelta(seconds=data["timezone"]))).strftime("%H:%M"),
                "sunset": datetime.fromtimestamp(data["sys"]["sunset"], tz=timezone(timedelta(seconds=data["timezone"]))).strftime("%H:%M"),
                "timestamp": datetime.now(tz=timezone(timedelta(seconds=data["timezone"]))).isoformat()
            }

            self.send_result_notification(
                status="SUCCESS",
                result=weather_result
            )

            # Publish app_interaction notification for weather app
            self._publish_weather_app_notification(weather_result, location)

        except requests.exceptions.Timeout:
            self.send_result_notification(
                status="FAILURE",
                error_message="Weather API request timed out"
            )
        except requests.exceptions.RequestException as e:
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Weather API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error fetching weather: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to fetch weather data: {str(e)}"
            )

    def _publish_weather_app_notification(self, weather_data: Dict[str, Any], location: str) -> None:
        """
        Publish weather data to the frontend weather app via app_interaction notification.

        Args:
            weather_data: The weather data to send
            location: Location that was queried
        """
        try:
            import requests
            import os

            # Get internal API key for authentication
            internal_api_key = None
            try:
                with open("/shared/internal_api_key", "r") as f:
                    internal_api_key = f.read().strip()
            except Exception as e:
                logger.warning(f"Could not load internal API key for weather app notification: {e}")
                return

            if not internal_api_key:
                logger.warning("Internal API key not found, skipping weather app notification")
                return

            # Get API Gateway URL from environment
            api_gateway_host = os.environ.get("API_GATEWAY_HOST", "api_gateway")
            api_gateway_port = os.environ.get("API_GATEWAY_PORT", "8000")
            api_gateway_url = f"http://{api_gateway_host}:{api_gateway_port}"

            # Prepare the notification data
            headers = {
                "Content-Type": "application/json",
                "X-Internal-Key": internal_api_key
            }

            # Extract session_id from the tool's context if available
            session_id = getattr(self, 'session_id', None)

            data = {
                "agent_id": "weather_agent",
                "app_name": "weather_app",
                "action": "weather_data_fetched",
                "result": weather_data,
                "session_id": session_id
            }

            # Publish via API Gateway notification endpoint
            response = requests.post(
                f"{api_gateway_url}/api/v1/notifications/app-interaction",
                json=data,
                headers=headers,
                timeout=5
            )

            if response.status_code == 200:
                logger.info(f"✅ Published weather data to weather_app for location: {location}")
            else:
                logger.warning(f"Failed to publish weather app notification: {response.status_code}")

        except Exception as e:
            # Don't crash if notification publishing fails - weather data still gets to agent
            logger.warning(f"Error publishing weather app notification: {e}")


class GetForecastTool(BaseTool):
    """
    Fetches weather forecast for a location using OpenWeatherMap API.
    """

    def __init__(self):
        super().__init__(
            name="get_forecast",
            description="Fetches 5-day weather forecast with 3-hour intervals"
        )
        self.api_key = os.environ.get("OPENWEATHERMAP_API_KEY")
        self.base_url = "https://api.openweathermap.org/data/2.5/forecast"

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate forecast arguments."""
        if "location" not in arguments:
            return False, "Missing required argument: 'location'"

        if not isinstance(arguments["location"], str):
            return False, f"'location' must be a string, got {type(arguments['location']).__name__}"

        if not arguments["location"].strip():
            return False, "'location' cannot be empty"

        if not self.api_key:
            return False, "OPENWEATHERMAP_API_KEY environment variable not set"

        if "days" in arguments:
            if not isinstance(arguments["days"], int):
                return False, f"'days' must be an integer, got {type(arguments['days']).__name__}"
            if arguments["days"] < 1 or arguments["days"] > 5:
                return False, "'days' must be between 1 and 5"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "get_forecast",
            "description": "Fetches 5-day weather forecast with 3-hour intervals",
            "parameters": [
                {
                    "name": "location",
                    "type": "str",
                    "description": "City name, country, or location",
                    "required": True
                },
                {
                    "name": "days",
                    "type": "int",
                    "description": "Number of days to forecast (1-5)",
                    "required": False
                },
                {
                    "name": "units",
                    "type": "str",
                    "description": "Temperature units - 'metric' or 'imperial'",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Fetch weather forecast for the specified location.

        Args:
            arguments: Must contain 'location', optionally 'days' (1-5) and 'units'
        """
        location = arguments["location"]
        days = arguments.get("days", 5)
        units = arguments.get("units", "metric")

        try:
            params = {
                "q": location,
                "appid": self.api_key,
                "units": units,
                "cnt": days * 8  # 8 forecasts per day (3-hour intervals)
            }

            response = requests.get(self.base_url, params=params, timeout=10)

            if response.status_code == 404:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Location '{location}' not found"
                )
                return

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

            self.send_result_notification(
                status="SUCCESS",
                result=forecast_result
            )

        except requests.exceptions.Timeout:
            self.send_result_notification(
                status="FAILURE",
                error_message="Weather API request timed out"
            )
        except requests.exceptions.RequestException as e:
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Weather API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error fetching forecast: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to fetch forecast data: {str(e)}"
            )


class GetWeatherByCoordinatesTool(BaseTool):
    """
    Fetches weather data using geographic coordinates.
    """

    def __init__(self):
        super().__init__(
            name="get_weather_by_coordinates",
            description="Fetches weather data using latitude and longitude coordinates"
        )
        self.api_key = os.environ.get("OPENWEATHERMAP_API_KEY")
        self.base_url = "https://api.openweathermap.org/data/2.5/weather"

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate coordinates arguments."""
        if "latitude" not in arguments or "longitude" not in arguments:
            return False, "Missing required arguments: 'latitude' and 'longitude'"

        try:
            lat = float(arguments["latitude"])
            lon = float(arguments["longitude"])

            if not (-90 <= lat <= 90):
                return False, "Latitude must be between -90 and 90"

            if not (-180 <= lon <= 180):
                return False, "Longitude must be between -180 and 180"

        except (TypeError, ValueError):
            return False, "Latitude and longitude must be numbers"

        if not self.api_key:
            return False, "OPENWEATHERMAP_API_KEY environment variable not set"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "get_weather_by_coordinates",
            "description": "Fetches weather data using latitude and longitude coordinates",
            "parameters": [
                {
                    "name": "latitude",
                    "type": "float",
                    "description": "Latitude coordinate (-90 to 90)",
                    "required": True
                },
                {
                    "name": "longitude",
                    "type": "float",
                    "description": "Longitude coordinate (-180 to 180)",
                    "required": True
                },
                {
                    "name": "units",
                    "type": "str",
                    "description": "Temperature units - 'metric' or 'imperial'",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Fetch weather data for the specified coordinates.

        Args:
            arguments: Must contain 'latitude' and 'longitude'
        """
        lat = float(arguments["latitude"])
        lon = float(arguments["longitude"])
        units = arguments.get("units", "metric")

        try:
            params = {
                "lat": lat,
                "lon": lon,
                "appid": self.api_key,
                "units": units
            }

            response = requests.get(self.base_url, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()

            # Similar formatting to GetWeatherTool but with emphasis on coordinates
            weather_result = {
                "coordinates": {
                    "latitude": lat,
                    "longitude": lon
                },
                "location": data.get("name", "Unknown"),
                "country": data["sys"].get("country", "Unknown"),
                "temperature": {
                    "current": data["main"]["temp"],
                    "feels_like": data["main"]["feels_like"],
                    "min": data["main"]["temp_min"],
                    "max": data["main"]["temp_max"],
                    "unit": "°C" if units == "metric" else "°F"
                },
                "condition": {
                    "main": data["weather"][0]["main"],
                    "description": data["weather"][0]["description"].capitalize()
                },
                "atmosphere": {
                    "humidity": f"{data['main']['humidity']}%",
                    "pressure": f"{data['main']['pressure']} hPa"
                },
                "wind": {
                    "speed": f"{data['wind']['speed']} {'m/s' if units == 'metric' else 'mph'}",
                    "direction": data["wind"].get("deg", "N/A")
                },
                "timestamp": datetime.now().isoformat()
            }

            self.send_result_notification(
                status="SUCCESS",
                result=weather_result
            )

        except requests.exceptions.RequestException as e:
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Weather API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error fetching weather by coordinates: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to fetch weather data: {str(e)}"
            )


class GetUVIndexTool(BaseTool):
    """
    Fetches UV index data for a location.
    Note: Requires OpenWeatherMap One Call API (may need subscription).
    """

    def __init__(self):
        super().__init__(
            name="get_uv_index",
            description="Fetches current UV index for a location"
        )
        self.api_key = os.environ.get("OPENWEATHERMAP_API_KEY")
        # First get coordinates, then use One Call API
        self.geocoding_url = "https://api.openweathermap.org/geo/1.0/direct"
        self.onecall_url = "https://api.openweathermap.org/data/2.5/onecall"

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate location argument."""
        if "location" not in arguments:
            return False, "Missing required argument: 'location'"

        if not isinstance(arguments["location"], str):
            return False, f"'location' must be a string"

        if not self.api_key:
            return False, "OPENWEATHERMAP_API_KEY environment variable not set"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "get_uv_index",
            "description": "Fetches current UV index and safety recommendations for a location",
            "parameters": [
                {
                    "name": "location",
                    "type": "str",
                    "description": "City name, country, or location",
                    "required": True
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Fetch UV index for the specified location.

        Args:
            arguments: Must contain 'location'
        """
        location = arguments["location"]

        try:
            # First, get coordinates for the location
            geo_params = {
                "q": location,
                "limit": 1,
                "appid": self.api_key
            }

            geo_response = requests.get(self.geocoding_url, params=geo_params, timeout=10)
            geo_response.raise_for_status()
            geo_data = geo_response.json()

            if not geo_data:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Location '{location}' not found"
                )
                return

            lat = geo_data[0]["lat"]
            lon = geo_data[0]["lon"]

            # Now get UV data using One Call API
            uv_params = {
                "lat": lat,
                "lon": lon,
                "exclude": "minutely,hourly,daily,alerts",
                "appid": self.api_key
            }

            uv_response = requests.get(self.onecall_url, params=uv_params, timeout=10)

            if uv_response.status_code == 401:
                # Fallback to current weather API which includes basic UV in some cases
                self.send_result_notification(
                    status="FAILURE",
                    error_message="UV Index requires OpenWeatherMap One Call API subscription"
                )
                return

            uv_response.raise_for_status()
            uv_data = uv_response.json()

            uv_index = uv_data["current"].get("uvi", "N/A")

            # Categorize UV index
            if isinstance(uv_index, (int, float)):
                if uv_index <= 2:
                    risk = "Low"
                    recommendation = "No protection needed"
                elif uv_index <= 5:
                    risk = "Moderate"
                    recommendation = "Seek shade during midday hours"
                elif uv_index <= 7:
                    risk = "High"
                    recommendation = "Protection required - hat and sunscreen"
                elif uv_index <= 10:
                    risk = "Very High"
                    recommendation = "Extra protection needed"
                else:
                    risk = "Extreme"
                    recommendation = "Avoid outdoor exposure"
            else:
                risk = "Unknown"
                recommendation = "UV data not available"

            result = {
                "location": location,
                "coordinates": {"lat": lat, "lon": lon},
                "uv_index": uv_index,
                "risk_level": risk,
                "recommendation": recommendation,
                "timestamp": datetime.now().isoformat()
            }

            self.send_result_notification(
                status="SUCCESS",
                result=result
            )

        except requests.exceptions.RequestException as e:
            self.send_result_notification(
                status="FAILURE",
                error_message=f"API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error fetching UV index: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to fetch UV index: {str(e)}"
            )


class GetAirQualityTool(BaseTool):
    """
    Fetches air quality data for a location.
    Uses OpenWeatherMap Air Pollution API (free tier available).
    """

    def __init__(self):
        super().__init__(
            name="get_air_quality",
            description="Fetches current air quality and pollution data for a location"
        )
        self.api_key = os.environ.get("OPENWEATHERMAP_API_KEY")
        self.geocoding_url = "https://api.openweathermap.org/geo/1.0/direct"
        self.air_quality_url = "https://api.openweathermap.org/data/2.5/air_pollution"

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate location argument."""
        if "location" not in arguments:
            return False, "Missing required argument: 'location'"

        if not isinstance(arguments["location"], str):
            return False, f"'location' must be a string"

        if not self.api_key:
            return False, "OPENWEATHERMAP_API_KEY environment variable not set"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "get_air_quality",
            "description": "Fetches current air quality and pollution data for a location",
            "parameters": [
                {
                    "name": "location",
                    "type": "str",
                    "description": "City name, country, or location",
                    "required": True
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Fetch air quality data for the specified location.

        Args:
            arguments: Must contain 'location'
        """
        location = arguments["location"]

        try:
            # First, get coordinates for the location
            geo_params = {
                "q": location,
                "limit": 1,
                "appid": self.api_key
            }

            geo_response = requests.get(self.geocoding_url, params=geo_params, timeout=10)
            geo_response.raise_for_status()
            geo_data = geo_response.json()

            if not geo_data:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Location '{location}' not found"
                )
                return

            lat = geo_data[0]["lat"]
            lon = geo_data[0]["lon"]

            # Get air quality data
            air_params = {
                "lat": lat,
                "lon": lon,
                "appid": self.api_key
            }

            air_response = requests.get(self.air_quality_url, params=air_params, timeout=10)
            air_response.raise_for_status()
            air_data = air_response.json()

            if air_data["list"]:
                pollution = air_data["list"][0]
                aqi = pollution["main"]["aqi"]

                # AQI interpretation
                aqi_levels = {
                    1: ("Good", "Air quality is satisfactory"),
                    2: ("Fair", "Acceptable air quality"),
                    3: ("Moderate", "May affect sensitive groups"),
                    4: ("Poor", "May affect healthy people"),
                    5: ("Very Poor", "Health warnings of emergency conditions")
                }

                level, description = aqi_levels.get(aqi, ("Unknown", "No data available"))

                result = {
                    "location": location,
                    "coordinates": {"lat": lat, "lon": lon},
                    "air_quality_index": aqi,
                    "level": level,
                    "description": description,
                    "components": {
                        "co": f"{pollution['components']['co']} μg/m³",
                        "no": f"{pollution['components']['no']} μg/m³",
                        "no2": f"{pollution['components']['no2']} μg/m³",
                        "o3": f"{pollution['components']['o3']} μg/m³",
                        "so2": f"{pollution['components']['so2']} μg/m³",
                        "pm2_5": f"{pollution['components']['pm2_5']} μg/m³",
                        "pm10": f"{pollution['components']['pm10']} μg/m³",
                        "nh3": f"{pollution['components']['nh3']} μg/m³"
                    },
                    "timestamp": datetime.now().isoformat()
                }

                self.send_result_notification(
                    status="SUCCESS",
                    result=result
                )
            else:
                self.send_result_notification(
                    status="FAILURE",
                    error_message="No air quality data available for this location"
                )

        except requests.exceptions.RequestException as e:
            self.send_result_notification(
                status="FAILURE",
                error_message=f"API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error fetching air quality: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to fetch air quality data: {str(e)}"
            )


# Export all weather tools
WEATHER_TOOLS = [
    GetWeatherTool,
    GetForecastTool,
    GetWeatherByCoordinatesTool,
    GetUVIndexTool,
    GetAirQualityTool
]