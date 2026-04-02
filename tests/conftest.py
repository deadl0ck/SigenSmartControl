import sys
import os
import logging
import importlib

def mask(val, key=None):
	if key and key.upper() in ("SIGEN_PASSWORD",):
		return "***MASKED***"
	if isinstance(val, str) and ("PASS" in val or "SECRET" in val or "TOKEN" in val):
		return val[:2] + "***MASKED***" + val[-2:]
	return val

def pytest_configure():
	"""
	Log all constants from constants.py once at the start of the test session.
	"""
	# Ensure .env is loaded for all tests
	logger = logging.getLogger("test")
	try:
		from dotenv import load_dotenv
		load_dotenv()
	except ImportError:
		logger.warning("[TEST] python-dotenv not installed; .env will not be loaded for tests.")

	# Ensure logging is set up for pytest runs
	logging.basicConfig(
		level=logging.INFO,
		format='%(asctime)s %(levelname)s %(message)s',
		stream=sys.stdout,
		force=True
	)
	# Log only relevant environment variables (masking sensitive info)
	relevant_env_vars = [
		"SIGEN_USERNAME", "SIGEN_PASSWORD", "SIGEN_LATITUDE", "SIGEN_LONGITUDE"
	]
	logger.info("[TEST] Loaded relevant environment variables:")
	for k in relevant_env_vars:
		v = os.environ.get(k)
		if v is None:
			logger.info(f"[TEST] ENV {k} = [NOT SET]")
		else:
			logger.info(f"[TEST] ENV {k} = {mask(v, k)}")
	try:
		constants = importlib.import_module("constants")
		logger.info("[TEST] Loaded constants from constants.py:")
		for k in dir(constants):
			if k.isupper():
				v = getattr(constants, k)
				logger.info(f"[TEST] {k} = {mask(v)}")
	except Exception as e:
		logger.warning(f"[TEST] Could not load constants.py for logging: {e}")

# Shared pytest fixtures can go here

import pytest
import logging

def pytest_terminal_summary(terminalreporter):
	"""
	Print a summary at the end of the test run, including total tests, pass/fail/skip counts,
	and a recap of all [RESULT] lines from the log output.
	"""
	total = terminalreporter._numcollected
	passed = len(terminalreporter.stats.get('passed', []))
	failed = len(terminalreporter.stats.get('failed', []))
	skipped = len(terminalreporter.stats.get('skipped', []))

	terminalreporter.write_sep("=", "Sigen Test Suite Summary")
	terminalreporter.write_line(f"Total tests run: {total}")
	terminalreporter.write_line(f"Passed: {passed}")
	terminalreporter.write_line(f"Failed: {failed}")
	terminalreporter.write_line(f"Skipped: {skipped}")

	# Recap all [RESULT] lines from the captured log output
	result_lines = []
	for rep in terminalreporter.getreports("passed") + terminalreporter.getreports("failed"):
		caplog = getattr(rep, "caplog", None)
		if caplog:
			for line in caplog.splitlines():
				if "[RESULT]" in line:
					result_lines.append(line.strip())

	# If caplog is not available, try to parse from terminalreporter sections
	if not result_lines:
		sections = getattr(terminalreporter, "sections", [])
		for secname, content in sections:
			if "Captured log" in secname:
				for line in content.splitlines():
					if "[RESULT]" in line:
						result_lines.append(line.strip())

	if result_lines:
		terminalreporter.write_sep("-", "Scenario Results Recap")
		for line in result_lines:
			terminalreporter.write_line(line)
	else:
		terminalreporter.write_line("No [RESULT] lines found in logs.")

	terminalreporter.write_sep("=", f"Test run complete: {passed} passed, {failed} failed, {skipped} skipped.")