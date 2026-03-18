from http.server import BaseHTTPRequestHandler
import json
import sys
import os


def try_import(name):
    try:
        __import__(name)
        return "ok"
    except Exception as e:
        return str(e)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        results = {
            "flask": try_import("flask"),
            "supabase": try_import("supabase"),
            "cryptography": try_import("cryptography"),
            "google.oauth2": try_import("google.oauth2"),
            "googleapiclient": try_import("googleapiclient"),
            "anthropic": try_import("anthropic"),
            "reportlab": try_import("reportlab"),
            "flask_limiter": try_import("flask_limiter"),
        }
        # Try importing the actual app
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        os.environ.setdefault("SECRET_KEY", "x")
        os.environ.setdefault("SUPABASE_URL", "https://x.supabase.co")
        os.environ.setdefault("SUPABASE_KEY", "x")
        os.environ.setdefault("ADMIN_EMAILS", "x@x.com")
        os.environ.setdefault("APP_URL", "https://x.vercel.app")
        try:
            from app import app
            results["app_import"] = "ok"
        except Exception as e:
            results["app_import"] = repr(e)

        body = json.dumps(results, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)
