"""Minimal HTTP health server for deployment practice."""

from http.server import BaseHTTPRequestHandler, HTTPServer


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            body = b'{"status":"healthy"}\n'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        print(format % args)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), HealthHandler).serve_forever()