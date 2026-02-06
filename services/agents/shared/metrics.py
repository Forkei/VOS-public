"""
Shared Prometheus metrics server for VOS agents.

Note: Metrics are defined and incremented in the VOS SDK (vos_sdk/core/agent.py).
This module only provides the HTTP server to expose those metrics via generate_latest().
"""

from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from flask import Flask, Response
import threading
import logging

logger = logging.getLogger(__name__)


class MetricsServer:
    """Lightweight HTTP server for exposing Prometheus metrics."""

    def __init__(self, port=8080):
        self.port = port
        self.app = Flask(__name__)
        self.thread = None

        # Add metrics endpoint
        @self.app.route('/metrics')
        def metrics():
            return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

        @self.app.route('/health')
        def health():
            return {'status': 'healthy', 'service': 'agent_metrics'}

    def start(self):
        """Start the metrics server in a background thread."""
        def run_server():
            logger.info(f"🚀 Starting metrics server on port {self.port}")
            self.app.run(host='0.0.0.0', port=self.port, threaded=True)

        self.thread = threading.Thread(target=run_server, daemon=True)
        self.thread.start()
        logger.info(f"✅ Metrics server started at http://0.0.0.0:{self.port}/metrics")
