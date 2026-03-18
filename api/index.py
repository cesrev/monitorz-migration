"""
Vercel serverless entry point for Monitorz Flask app.
Wraps the WSGI Flask app in a BaseHTTPRequestHandler since
Vercel Python serverless works best with that interface.
"""
from http.server import BaseHTTPRequestHandler
import io
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app as flask_app


class handler(BaseHTTPRequestHandler):

    def _handle(self):
        # Read body
        content_length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(content_length) if content_length else b""

        # Build WSGI environ
        parsed_path = self.path.split("?", 1)
        environ = {
            "REQUEST_METHOD": self.command,
            "PATH_INFO": parsed_path[0],
            "QUERY_STRING": parsed_path[1] if len(parsed_path) > 1 else "",
            "CONTENT_TYPE": self.headers.get("content-type", ""),
            "CONTENT_LENGTH": str(content_length),
            "SERVER_NAME": self.headers.get("host", "localhost").split(":")[0],
            "SERVER_PORT": "443",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "https",
            "wsgi.input": io.BytesIO(body),
            "wsgi.errors": sys.stderr,
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }

        # Forward all HTTP headers
        for key, value in self.headers.items():
            key_upper = key.upper().replace("-", "_")
            if key_upper in ("CONTENT_TYPE", "CONTENT_LENGTH"):
                environ[key_upper] = value
            else:
                environ["HTTP_" + key_upper] = value

        # Call Flask WSGI app
        response_info = {}
        chunks = []

        def start_response(status, response_headers, exc_info=None):
            response_info["status"] = status
            response_info["headers"] = response_headers

        result = flask_app(environ, start_response)
        try:
            for chunk in result:
                chunks.append(chunk)
        finally:
            if hasattr(result, "close"):
                result.close()

        # Send response
        status_code = int(response_info["status"].split(" ", 1)[0])
        self.send_response(status_code)
        for name, value in response_info.get("headers", []):
            self.send_header(name, value)
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(chunk)

    def do_GET(self):     self._handle()
    def do_POST(self):    self._handle()
    def do_PUT(self):     self._handle()
    def do_DELETE(self):  self._handle()
    def do_PATCH(self):   self._handle()
    def do_OPTIONS(self): self._handle()
    def do_HEAD(self):    self._handle()
