"""Integration for GREEN-GRID Shiny app forecast retrieval.

This module provides utilities to programmatically access the GREEN-GRID
solar forecast app at https://greengrid.shinyapps.io/greengrid_energy_app/.

The app is Shiny-based (RStudio Connect) and requires browser automation
to fill inputs and retrieve the 1-day forecast results.

Setup:
- Install playwright: pip install playwright
- Install browser: playwright install chromium
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class GreenGridForecast:
    """Query GREEN-GRID Shiny app for solar forecast.

    GREEN-GRID uses advanced modeling to estimate hourly solar power
    for Irish homes. This class automates the browser interaction needed
    to fill the input form and retrieve the 1-day forecast.

    Note:
    - The app is at https://greengrid.shinyapps.io/greengrid_energy_app/
    - Input form requires: Eircode, panel direction, roof pitch, panel count
    - Forecast appears in "1 Day Forecast" tab after Submit
    """

    # Shiny input element IDs from app HTML
    EIRCODE_NOT_FOUND = "Eircode lookup requires browser automation or direct geocoding"
    FIELD_IDS = {
        "direction": "Panel facing direction (N/S/E/W/NE/SE/SW/NW)",
        "roof_angle": "Roof pitch in degrees (15-80)",
        "number_panel": "Number of solar panels (4-300)",
        "input_dataframe": "Submit button to trigger forecast calculation",
        "forecast_tab": "Tab containing 1-day forecast results",
        "forecast_sol_table": "DataTable with hourly forecast values",
        "forecast_sol_plot": "Plot showing forecast curve",
    }

    DIRECTION_MAP = {
        "North": "North(N)",
        "N": "North(N)",
        "South": "South(S)",
        "S": "South(S)",
        "East": "East(E)",
        "E": "East(E)",
        "West": "West(W)",
        "W": "West(W)",
        "NorthEast": "North-East(NE)",
        "Northeast": "North-East(NE)",
        "NE": "North-East(NE)",
        "SouthEast": "South-East(SE)",
        "Southeast": "South-East(SE)",
        "SE": "South-East(SE)",
        "SouthWest": "South-West(SW)",
        "Southwest": "South-West(SW)",
        "SW": "South-West(SW)",
        "NorthWest": "North-West(NW)",
        "Northwest": "North-West(NW)",
        "NW": "North-West(NW)",
    }

    def __init__(self) -> None:
        """Initialize GREEN-GRID forecast provider."""
        self.logger = logger
        self.app_url = "https://greengrid.shinyapps.io/greengrid_energy_app/"
        self.playwright_installed = False
        self._check_playwright()

    def _check_playwright(self) -> None:
        """Check if playwright is available for browser automation."""
        try:
            import playwright  # noqa: F401
            self.playwright_installed = True
            self.logger.info("[GREEN-GRID] Playwright is available for browser automation")
        except ImportError:
            self.logger.warning(
                "[GREEN-GRID] Playwright not installed. "
                "To use GREEN-GRID forecast, install it: pip install playwright && playwright install"
            )

    def normalize_direction(self, direction: str) -> str | None:
        """Normalize panel direction input to Shiny app format.

        Args:
            direction: Direction name or abbreviation (e.g., 'SE', 'South-East').

        Returns:
            Normalized direction suitable for Shiny select input, or None if invalid.
        """
        return self.DIRECTION_MAP.get(direction.strip())

    async def fetch_forecast(
        self,
        eircode: str,
        direction: str,
        roof_pitch_degrees: int,
        num_panels: int,
    ) -> dict[str, Any] | None:
        """Fetch 1-day forecast from GREEN-GRID app via browser automation.

        Args:
            eircode: Irish Eircode (e.g., 'N91 F752')
            direction: Panel direction (e.g., 'SE', 'South-East')
            roof_pitch_degrees: Roof pitch in degrees (15-80)
            num_panels: Number of solar panels (4-300)

        Returns:
            Dict with 'timestamp', 'forecast_kwh', 'hourly_savings', or None if error.
        """
        if not self.playwright_installed:
            self.logger.error(
                "[GREEN-GRID] Playwright required. Install: pip install playwright"
            )
            return None

        norm_direction = self.normalize_direction(direction)
        if norm_direction is None:
            self.logger.error(f"[GREEN-GRID] Invalid direction: {direction}")
            return None

        if not (15 <= roof_pitch_degrees <= 80):
            self.logger.error(f"[GREEN-GRID] Roof pitch must be 15-80, got {roof_pitch_degrees}")
            return None

        if not (4 <= num_panels <= 300):
            self.logger.error(f"[GREEN-GRID] Num panels must be 4-300, got {num_panels}")
            return None

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.logger.error("[GREEN-GRID] Failed to import playwright async_api")
            return None

        forecast_data: dict[str, Any]= {"captured_at": datetime.now().isoformat()}

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()

                self.logger.info(f"[GREEN-GRID] Loading app: {self.app_url}")
                # Use load event instead of networkidle - Shiny apps may have persistent connections
                # that prevent networkidle. This gives the app up to 60 seconds to load
                try:
                    await page.goto(self.app_url, wait_until="load", timeout=60000)
                except Exception as exc:
                    self.logger.warning(f"[GREEN-GRID] Page load warning: {exc}. Continuing anyway...")

                # Wait for Shiny to initialize
                # The app uses Selectize.js which hides the native select and creates a custom widget
                # Wait for the page body and any shiny initialization to complete
                self.logger.info("[GREEN-GRID] Waiting for Shiny app initialization...")
                await page.wait_for_load_state("domcontentloaded")
                
                # Wait for Selectize widgets to initialize
                # Use state="attached" instead of "visible" - element exists in DOM even if hidden
                await page.wait_for_selector(".selectize-control", timeout=30000, state="attached")
                
                # Small delay to let Selectize fully initialize and become interactive
                await asyncio.sleep(1)

                # Fill form fields
                self.logger.info(
                    f"[GREEN-GRID] Submitting: direction={norm_direction}, "
                    f"pitch={roof_pitch_degrees}, panels={num_panels}"
                )

                # Set direction dropdown (Selectize.js widget)
                # Use JavaScript to set the value since Selectize hijacks the native select
                self.logger.info("[GREEN-GRID] Setting direction...")
                try:
                    await page.evaluate(f"""
                        document.querySelector('#direction').selectize.setValue('{norm_direction}');
                    """)
                    self.logger.info(f"[GREEN-GRID] Direction set to {norm_direction}")
                except Exception as exc:
                    self.logger.warning(f"[GREEN-GRID] Direction setting warning: {exc}")
                
                # Wait for form to be ready after setting direction
                await asyncio.sleep(0.5)
                
                # Set roof pitch using JavaScript (without scrolling - element may be hidden)
                self.logger.info("[GREEN-GRID] Setting roof pitch...")
                try:
                    await page.evaluate(f"""
                        var elem = document.getElementById('roof_angle');
                        if (elem) {{
                            elem.value = '{roof_pitch_degrees}';
                            elem.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            elem.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        }}
                    """)
                    self.logger.info(f"[GREEN-GRID] Roof pitch set to {roof_pitch_degrees}°")
                except Exception as exc:
                    self.logger.warning(f"[GREEN-GRID] Roof pitch setting warning: {exc}")
                
                # Set panel count using JavaScript
                self.logger.info("[GREEN-GRID] Setting panel count...")
                try:
                    await page.evaluate(f"""
                        var elem = document.getElementById('number_panel');
                        if (elem) {{
                            elem.value = '{num_panels}';
                            elem.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            elem.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        }}
                    """)
                    self.logger.info(f"[GREEN-GRID] Panel count set to {num_panels}")
                except Exception as exc:
                    self.logger.warning(f"[GREEN-GRID] Panel count setting warning: {exc}")

# Click Submit button using JavaScript
                self.logger.info("[GREEN-GRID] Submitting form...")
                try:
                    await page.evaluate("""
                        var btn = document.getElementById('input_dataframe');
                        if (btn) btn.click();
                    """)
                except Exception as exc:
                    self.logger.warning(f"[GREEN-GRID] Form submit warning: {exc}")

                # Wait for forecast tab to become available (when calculation completes)
                # This can take a while as the app calculates the forecast
                self.logger.info("[GREEN-GRID] Calculating forecast (this may take 20-60 seconds)...")
                # Wait for the table to be attached (not necessarily visible - it might be in a hidden div)
                await page.wait_for_selector("#forecast_sol_table", timeout=120000, state="attached")
                
                # The table exists but might still be calculating. Wait for it to stop recalculating
                self.logger.info("[GREEN-GRID] Waiting for calculation to complete...")
                # Poll every 2 seconds to check if recalculating class is gone
                for attempt in range(60):  # Try for up to 2 minutes
                    try:
                        is_recalculating = await page.query_selector(".recalculating")
                        if not is_recalculating:
                            self.logger.info("[GREEN-GRID] Calculation complete")
                            break
                        await asyncio.sleep(2)
                    except Exception:
                        break

                # Click forecast tab to navigate to results (if needed)
                # The app might auto-show the forecast, so this is optional
                try:
                    # Try to find and click a "1 Day Forecast" tab button
                    forecast_tab = await page.query_selector('a[href="#tab-forecast"], [data-value="1_day_forecast"], .nav-link:has-text("1 Day")')
                    if forecast_tab:
                        await forecast_tab.click(timeout=5000)
                        self.logger.info("[GREEN-GRID] Clicked forecast tab")
                except Exception:
                    # Tab may already be visible, continue anyway
                    self.logger.debug("[GREEN-GRID] No forecast tab found or already visible")
                    pass

                # Extract forecast table data
                table_rows = await page.query_selector_all(
                    "#forecast_sol_table tbody tr"
                )

                if table_rows:
                    self.logger.info(f"[GREEN-GRID] Found {len(table_rows)} forecast rows")
                    forecast_points = []
                    for row in table_rows:
                        cells = await row.query_selector_all("td")
                        if len(cells) >= 3:
                            date_text = await cells[0].text_content()
                            time_text = await cells[1].text_content()
                            forecast_kwh_text = await cells[2].text_content()

                            try:
                                forecast_kwh = float(forecast_kwh_text.strip() or "0")
                                forecast_points.append(
                                    {
                                        "date": date_text.strip(),
                                        "time": time_text.strip(),
                                        "forecast_kwh": forecast_kwh,
                                    }
                                )
                            except ValueError:
                                continue

                    if forecast_points:
                        forecast_data["forecast_points"] = forecast_points
                        forecast_data["total_forecast_kwh"] = sum(
                            p["forecast_kwh"] for p in forecast_points
                        )
                        forecast_data["inputs"] = {
                            "eircode": eircode,
                            "direction": direction,
                            "roof_pitch_degrees": roof_pitch_degrees,
                            "num_panels": num_panels,
                        }
                        self.logger.info(
                            f"[GREEN-GRID] Retrieved {len(forecast_points)} forecast points"
                        )
                        await browser.close()
                        return forecast_data

                self.logger.warning("[GREEN-GRID] No forecast table data found")
                await browser.close()
                return None

        except Exception as exc:
            self.logger.error(f"[GREEN-GRID] Forecast retrieval failed: {exc}")
            self.logger.debug(f"[GREEN-GRID] Exception details: {type(exc).__name__}: {exc}")
            return None

    def __str__(self) -> str:
        return (
            f"GreenGridForecast(app={self.app_url}, "
            f"playwright_ready={self.playwright_installed})"
        )


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(level=logging.INFO)

    async def main() -> None:
        provider = GreenGridForecast()
        print(provider)

        if not provider.playwright_installed:
            print("Playwright not available. Install with:")
            print("  pip install playwright && playwright install")
            return

        forecast = await provider.fetch_forecast(
            eircode="N91 F752",
            direction="SE",
            roof_pitch_degrees=27,
            num_panels=20,
        )

        if forecast:
            print("\nForecast data:")
            print(json.dumps(forecast, indent=2))
        else:
            print("Failed to retrieve forecast")

    asyncio.run(main())
